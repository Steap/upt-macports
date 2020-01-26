from io import StringIO
import unittest
from unittest import mock
import upt
from upt_macports.upt_macports import DependsUpdater
from upt_macports.upt_macports import DependsUpdaterManager
from upt_macports.upt_macports import MacPortsBackend


class TestMacPortsBackend(unittest.TestCase):
    def setUp(self):
        self.macports_backend = MacPortsBackend()
        self.macports_backend.frontend = 'pypi'

    @mock.patch('upt_macports.upt_macports.MacPortsBackend.package_versions',
                return_value=['1.2'])
    def test_current_version(self, m_package_versions):
        version = self.macports_backend.current_version(mock.Mock(), 'foo')
        self.assertEqual(version, '1.2')

    @mock.patch('upt_macports.upt_macports.MacPortsBackend.package_versions',
                return_value=[])
    @mock.patch('upt.Backend.current_version', return_value='1.2')
    def test_current_version_fallback(self, m_current_version,
                                      m_package_versions):
        version = self.macports_backend.current_version(mock.Mock(), 'foo')
        self.assertEqual(version, '1.2')

    def test_unhandled_frontend(self):
        upt_pkg = upt.Package('foo', '42')
        upt_pkg.frontend = 'invalid frontend'
        with self.assertRaises(upt.UnhandledFrontendError):
            self.macports_backend.create_package(upt_pkg)

    def test_update_version(self):
        old = '1.2.3'
        new = '4.5.6'

        github_before = 'github.setup        gwpy ligotimegps 1.2.3 v'
        github_after = 'github.setup        gwpy ligotimegps 4.5.6 v'
        ruby_before = 'ruby.setup          rails 1.2.3 gem {} rubygems ruby19'
        ruby_after = 'ruby.setup          rails 4.5.6 gem {} rubygems ruby19'
        test_cases = {
            github_before: github_after,
            ruby_before: ruby_after,
            'version             1.2.3': 'version             4.5.6',
            'foo bar baz': 'foo bar baz',
        }
        for before, after in test_cases.items():
            out = self.macports_backend._update_version(before, old, new)
            self.assertEqual(out, after)

    def test_update_revision(self):
        test_cases = {
            'revision      1\n': 'revision      0\n',
            'foo\n': 'foo\n',
        }
        for before, after in test_cases.items():
            self.assertEqual(self.macports_backend._update_revision(before),
                             after)

    def test_update_archives(self):
        oldrmd = '91bf89c0493ad2caa8ed29372972e2e887f84bb8'
        oldsha = '2e50876bcdd74517e7b71f3e7a76102050edec255b3983403f1a63e7c8a41e7a'  # noqa
        newrmd = '770c41f726e57b64e2c27266e6b0cf8b7bf895ab'
        newsha = '2f52bbb095baa858b3273d851de5cc25a4470351bdfe675b2d5b997e3145c2c4'  # noqa
        oldsize = 42
        newsize = 1337
        old = upt.Archive('url', size=oldsize, rmd160=oldrmd, sha256=oldsha)
        new = upt.Archive('url', size=newsize, rmd160=newrmd, sha256=newsha)
        out = self.macports_backend._update_archives('foo', old, None)
        self.assertEqual(out, 'foo')
        out = self.macports_backend._update_archives('foo', None, new)
        self.assertEqual(out, 'foo')
        out = self.macports_backend._update_archives('foo', None, None)
        self.assertEqual(out, 'foo')

        test_cases = {
            f'checksums rmd160 {oldrmd} \\\n': f'checksums rmd160 {newrmd} \\\n',  # noqa
            f'checksums sha256 {oldsha} \\\n': f'checksums sha256 {newsha} \\\n',  # noqa
            f'          rmd160 {oldrmd} \\\n': f'          rmd160 {newrmd} \\\n',  # noqa
            f'          sha256 {oldsha} \\\n': f'          sha256 {newsha} \\\n',  # noqa
            f'          rmd160 {oldrmd}\n': f'          rmd160 {newrmd}\n',
            f'          sha256 {oldsha}\n': f'          sha256 {newsha}\n',
            f'          size {oldsize}\n': f'          size {newsize}\n',
            'supported_archs noarch\n': 'supported_archs noarch\n',
        }
        for before, after in test_cases.items():
            out = self.macports_backend._update_archives(before, old, new)
            self.assertEqual(out, after)

    def test_update_portfile_content_beautifulsoup(self):
        old_portfile = '''\
version 4.6.0

checksums           rmd160  6452de577ef676636fb0be79eba9224cafd5622d \\
                    size    160846
if {${name} ne ${subport}} {
    depends_lib-append  port:py${python.version}-setuptools
}
'''
        expected_portfile = '''\
version 4.9.1

checksums           rmd160  b72ed53263f07c843ce34513a9d62128051e2fc3 \\
                    size    374759
if {${name} ne ${subport}} {
    depends_lib-append  port:py${python.version}-setuptools \\
                        port:py${python.version}-soupsieve
}
'''
        oldpkg = upt.Package('beautifulsoup4', '4.6.0')
        oldpkg.frontend = 'pypi'
        oldpkg.requirements = {}
        oldpkg.archives = [
            upt.Archive('url',
                        rmd160='6452de577ef676636fb0be79eba9224cafd5622d',
                        size=160846)
        ]
        newpkg = upt.Package('beautifulsoup4', '4.9.1')
        newpkg.frontend = 'pypi'
        newpkg.requirements = {
            'run': [
                upt.PackageRequirement('soupsieve'),
            ],
        }
        newpkg.archives = [
            upt.Archive('url',
                        rmd160='b72ed53263f07c843ce34513a9d62128051e2fc3',
                        size=374759)
        ]
        pdiff = upt.PackageDiff(oldpkg, newpkg)
        handle = StringIO(old_portfile)
        out = self.macports_backend._update_portfile_content(handle, pdiff)
        self.assertEqual(out, expected_portfile)

    def test_update_portfile_content_sunpy(self):
        # Upgrading sunpy from 0.3.1 to 1.1.3 has us:
        # 1) Reset the revision (from 1 to 0)
        # 2) Parse a big block of runtime dependencies
        old_portfile = '''\
version     0.3.1
revision    1

if {${name} ne ${subport}} {

    depends_build-append  port:py${python.version}-numpy

    depends_lib-append    port:py${python.version}-scipy \\
                          port:py${python.version}-matplotlib \\
                          port:py${python.version}-astropy \\
                          port:py${python.version}-pyqt4 \\
                          port:py${python.version}-suds \\
                          port:py${python.version}-pandas \\
                          port:py${python.version}-beautifulsoup4 \\
                          port:py${python.version}-configobj \\
                          port:py${python.version}-setuptools \\
                          port:py${python.version}-py
}
'''
        expected_portfile = '''\
version     1.1.3
revision    0

if {${name} ne ${subport}} {
    depends_test-append port:py${python.version}-hypothesis \\
                        port:py${python.version}-pytest \\
                        port:py${python.version}-pytest-doctestplus \\
                        port:py${python.version}-pytest-astropy \\
                        port:py${python.version}-pytest-cov \\
                        port:py${python.version}-pytest-mock \\
                        port:py${python.version}-tox \\
                        port:py${python.version}-tox-conda

    depends_build-append  port:py${python.version}-numpy

    depends_lib-append    port:py${python.version}-scipy \\
                          port:py${python.version}-matplotlib \\
                          port:py${python.version}-astropy \\
                          port:py${python.version}-pyqt4 \\
                          port:py${python.version}-suds \\
                          port:py${python.version}-pandas \\
                          port:py${python.version}-beautifulsoup4 \\
                          port:py${python.version}-configobj \\
                          port:py${python.version}-setuptools \\
                          port:py${python.version}-py \\
                          port:py${python.version}-numpy \\
                          port:py${python.version}-parfive
}
'''
        oldpkg = upt.Package('sunpy', '0.3.1')
        oldpkg.frontend = 'pypi'
        oldpkg.requirements = {
        }
        newpkg = upt.Package('beautifulsoup4', '1.1.3')
        newpkg.frontend = 'pypi'
        newpkg.requirements = {
            'run': [
                upt.PackageRequirement('numpy'),
                upt.PackageRequirement('parfive'),
            ],
            'test': [
                upt.PackageRequirement('hypothesis'),
                upt.PackageRequirement('pytest'),
                upt.PackageRequirement('pytest-doctestplus'),
                upt.PackageRequirement('pytest-astropy'),
                upt.PackageRequirement('pytest-cov'),
                upt.PackageRequirement('pytest-mock'),
                upt.PackageRequirement('tox'),
                upt.PackageRequirement('tox-conda'),
            ],
        }
        pdiff = upt.PackageDiff(oldpkg, newpkg)
        handle = StringIO(old_portfile)
        out = self.macports_backend._update_portfile_content(handle, pdiff)
        self.assertEqual(out, expected_portfile)

    def test_update_portfile_content_gwosc(self):
        # What is interesting about updating gwosc from 0.3.3 to 0.5.3 is:
        # 1) depends_lib-append must be removed completely
        # 2) depends_test-append must be added
        old_portfile = '''\
version 0.3.3

if {${name} ne ${subport}} {
    depends_build-append port:py${python.version}-setuptools
    depends_lib-append   port:py${python.version}-six
    livecheck.type      none
} else {
'''
        expected_portfile = '''\
version 0.5.3

if {${name} ne ${subport}} {
    depends_test-append port:py${python.version}-pytest \\
                        port:py${python.version}-pytest-cov \\
                        port:py${python.version}-pytest-socket
    depends_build-append port:py${python.version}-setuptools
    livecheck.type      none
} else {
'''
        oldpkg = upt.Package('gwosc', '0.3.3')
        oldpkg.frontend = 'pypi'
        oldpkg.requirements = {
            'run': [
                upt.PackageRequirement('six'),
            ],
        }
        newpkg = upt.Package('gwosc', '0.5.3')
        newpkg.frontend = 'pypi'
        newpkg.requirements = {
            'run': [],
            'test': [
                upt.PackageRequirement('pytest'),
                upt.PackageRequirement('pytest-cov'),
                upt.PackageRequirement('pytest-socket'),
            ],
        }
        pdiff = upt.PackageDiff(oldpkg, newpkg)
        handle = StringIO(old_portfile)
        out = self.macports_backend._update_portfile_content(handle, pdiff)
        self.assertEqual(out, expected_portfile)

    @mock.patch('builtins.open', new_callable=mock.mock_open,
                read_data='old Portfile')
    @mock.patch.object(MacPortsBackend, '_update_portfile_content',
                       return_value='New Portfile')
    def test_update_package(self, mock_update_portfile_content, mock_open):
        pdiff = mock.Mock()
        pdiff.new.frontend = 'pypi'
        self.macports_backend.update_package(pdiff)
        mock_open().write.assert_called_once_with('New Portfile')


class TestDependsUpdater(unittest.TestCase):
    def test_clean_depends_line(self):
        test_cases = [
            ('port:foo', 'port:foo'),
            ('port:foo\n', 'port:foo'),
            ('port:foo \\\n', 'port:foo'),
        ]

        for dirty_line, clean_line in test_cases:
            self.assertEqual(DependsUpdater._clean_depends_line(dirty_line),
                             clean_line)

    def test_update(self):
        old_pkg = upt.Package('foo', '1.0')
        old_pkg.requirements = {
            'run': [
                upt.PackageRequirement('always-there-run-req'),
                upt.PackageRequirement('deleted-run-req'),
                upt.PackageRequirement('deleted-run-req-never-included'),
            ]
        }
        new_pkg = upt.Package('foo', '2.0')
        new_pkg.requirements = {
            'run': [
                upt.PackageRequirement('already-in-macports-run-req'),
                upt.PackageRequirement('always-there-run-req'),
                upt.PackageRequirement('added-run-req'),
            ]
        }
        diff = upt.PackageDiff(old_pkg, new_pkg)
        du = DependsUpdater('run')
        du.deps = [
            'port:always-there-run-req',
            'port:already-in-macports-run-req',
            'port:deleted-run-req',
        ]
        du.update(diff, lambda x: x)
        expected_deps = [
            'port:always-there-run-req',
            'port:already-in-macports-run-req',
            'port:added-run-req',
        ]
        self.assertEqual(du.deps, expected_deps)

    def test_build_depends_line_no_deps(self):
        du = DependsUpdater('run')
        line = du.build_depends_line()
        self.assertEqual(line, '')

    def test_build_depends_line_single_space(self):
        current_depends_block = [
            'depends_build-append \\\n',
            '                     port:foo \\\n',
            '                     port:bar \\\n',
            '                     port:baz\n',
        ]
        du = DependsUpdater('build')
        for line in current_depends_block:
            du.process_line(line)
        new_line = du.build_depends_line()
        self.assertEqual(new_line, ''.join(current_depends_block))

    def test_build_depends_line_single_space_same_line(self):
        current_depends_block = [
            'depends_build-append port:foo \\\n',
            '                     port:bar \\\n',
            '                     port:baz\n',
        ]
        du = DependsUpdater('build')
        for line in current_depends_block:
            du.process_line(line)
        new_line = du.build_depends_line()
        self.assertEqual(new_line, ''.join(current_depends_block))

    def test_build_depends_line_multiple_spaces(self):
        current_depends_block = [
            'depends_test-append     \\\n',
            '                     port:foo \\\n',
            '                     port:bar \\\n',
            '                     port:baz\n',
        ]
        du = DependsUpdater('test')
        for line in current_depends_block:
            du.process_line(line)
        new_line = du.build_depends_line()
        self.assertEqual(new_line, ''.join(current_depends_block))

    def test_build_depends_line_multiple_spaces_same_line(self):
        current_depends_block = [
            'depends_test-append     port:foo \\\n',
            '                     port:bar \\\n',
            '                     port:baz\n',
        ]
        du = DependsUpdater('test')
        for line in current_depends_block:
            du.process_line(line)
        new_line = du.build_depends_line()
        self.assertEqual(new_line, ''.join(current_depends_block))


class TestDependsUpdaterManager(unittest.TestCase):
    def test_process_line_no_match(self):
        dum = DependsUpdaterManager(mock.Mock, lambda x: x)
        self.assertEqual(dum.process_line('version 13.37'), (False, None))

    def test_process_line_depends_block(self):
        oldpkg = upt.Package('foo', '1.0')
        oldpkg.requirements = {
            'run': [
                upt.PackageRequirement('foo'),
                upt.PackageRequirement('bar'),
            ],
        }
        newpkg = upt.Package('foo', '2.0')
        newpkg.requirements = {
            'run': [
                upt.PackageRequirement('bar'),
            ],
        }
        pdiff = upt.PackageDiff(oldpkg, newpkg)
        dum = DependsUpdaterManager(pdiff, lambda x: x)
        self.assertEqual(dum.process_line('depends_lib-append port:foo \\\n'),
                         (True, None))
        self.assertEqual(dum.process_line('    port:bar\n'),
                         (True, 'depends_lib-append port:bar\n'))

    def test_flush(self):
        oldpkg = upt.Package('foo', '1.0')
        oldpkg.requirements = {}
        newpkg = upt.Package('foo', '2.0')
        newpkg.requirements = {
            'build': [upt.PackageRequirement('bar')],
        }
        pdiff = upt.PackageDiff(oldpkg, newpkg)
        dum = DependsUpdaterManager(pdiff, lambda x: f'{x.name.upper()}')
        expected = 'depends_build-append port:BAR\n'
        self.assertEqual(dum.flush(), expected)

    def test_flush_nothing_left(self):
        dum = DependsUpdaterManager(mock.Mock, lambda x: x)
        dum._depends_updaters = []
        self.assertEqual(dum.flush(), '')


class TestMacPortsPackageExist(unittest.TestCase):
    def setUp(self):
        self.macports_backend = MacPortsBackend()
        self.macports_backend.upt_pkg = upt.Package('foo', '42')
        self.macports_backend.frontend = 'pypi'

    @mock.patch('subprocess.getoutput')
    def test_port_found(self, mock_sub):
        expected = ['0.123']
        mock_sub.return_value = 'version: 0.123'
        self.assertEqual(
            self.macports_backend.package_versions('foo'), expected)

    @mock.patch('subprocess.getoutput')
    def test_port_not_found(self, mock_sub):
        expected = []
        mock_sub.return_value = 'Error: fake-error'
        self.assertEqual(
            self.macports_backend.package_versions('foo'), expected)

    @mock.patch('subprocess.getoutput')
    def test_port_outdated(self, mock_sub):
        expected = ['0.123']
        mock_sub.return_value = 'Warning: fake-warning \nversion: 0.123'
        self.assertEqual(
            self.macports_backend.package_versions('foo'), expected)

    @mock.patch('subprocess.getoutput')
    def test_port_error(self, mock_sub):
        mock_sub.return_value = 'bash: port: command not found'
        with self.assertRaises(SystemExit):
            self.macports_backend.package_versions('foo')


class TestMacPortsCpanVersion(unittest.TestCase):
    def setUp(self):
        self.macports_backend = MacPortsBackend()
        self.macports_backend.upt_pkg = upt.Package('foo', '42')
        self.macports_backend.frontend = 'cpan'

    def test_version_conversion(self):
        converted = ['1', '1.2.3', '1.200.0', '1.200.0', '1.20.0',
                     '1', '1.2.3', '1.200.0', '1.200.0', '1.20.0']
        upstream = ['1', '1.2.3', '1.2', '1.20', '1.02',
                    'v1', 'v1.2.3', 'v1.2', 'v1.20', 'v1.02']
        for mp_ver, cpan_ver in zip(upstream, converted):
            self.assertEqual(
                    self.macports_backend.standardize_CPAN_version(mp_ver),
                    cpan_ver)

    @mock.patch('upt.Backend.needs_requirement')
    def test_needs_requirement(self, mock_need_req):
        specifiers = {
            '>=42': '>=42',
            '<=42': '<=42',
            '!=42': '!=42',
            '==42': '==42',
            '>= 1.2, != 1.5, < 2.0': '>=1.200.0, !=1.500.0, <2.0.0',
            '>= 1.2, != 2, < 3.0': '>=1.200.0, !=2, <3.0.0'
        }

        for key, value in specifiers.items():
            req = upt.PackageRequirement('bar', key)
            self.macports_backend.needs_requirement(req, 'fake-phase')
            self.assertCountEqual(
                mock_need_req.call_args[0][0].specifier.split(', '),
                value.split(', ')
                )


if __name__ == '__main__':
    unittest.main()
