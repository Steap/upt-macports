import upt
import logging
import jinja2
import pkg_resources
import json
import requests
import os
import re
import subprocess
import sys
from packaging.specifiers import SpecifierSet


class MacPortsPackage(object):
    def __init__(self):
        self.logger = logging.getLogger('upt')

    def create_package(self, upt_pkg, output):
        self.upt_pkg = upt_pkg
        self.logger.info(f'Creating MacPorts package for {self.upt_pkg.name}')
        portfile_content = self._render_makefile_template()
        if output is None:
            print(portfile_content)
        else:
            self._create_output_directories(upt_pkg, output)
            self._create_portfile(portfile_content)

    def _create_output_directories(self, upt_pkg, output_dir):
        """Creates the directory layout required"""
        self.logger.info(f'Creating the directory structure in {output_dir}')
        folder_name = self._normalized_macports_folder(upt_pkg.name)
        self.output_dir = os.path.join(
            output_dir, self.category, folder_name)
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            self.logger.info(f'Created {self.output_dir}')
        except PermissionError:
            sys.exit(f'Cannot create {self.output_dir}: permission denied.')

    def _create_portfile(self, portfile_content):
        self.logger.info('Creating the Portfile')
        try:
            with open(os.path.join(self.output_dir, 'Portfile'), 'x',
                      encoding='utf-8') as f:
                f.write(portfile_content)
        except FileExistsError:
            sys.exit(f'Cannot create {self.output_dir}/Portfile: already exists.') # noqa

    def _render_makefile_template(self):
        env = jinja2.Environment(
            loader=jinja2.PackageLoader('upt_macports', 'templates'),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        env.filters['reqformat'] = self.jinja2_reqformat
        template = env.get_template(self.template)
        return template.render(pkg=self)

    @property
    def licenses(self):
        relpath = 'spdx2macports.json'
        filepath = pkg_resources.resource_filename(__name__, relpath)
        with open(filepath) as f:
            spdx2macports = json.loads(f.read())

        if not self.upt_pkg.licenses:
            self.logger.warning('No license found')
            return 'unknown  # no upstream license found'
        licenses = []
        for license in self.upt_pkg.licenses:
            try:
                if license.spdx_identifier == 'unknown':
                    warn = 'upt failed to detect license'
                    port_license = f'unknown  # {warn}'
                    self.logger.warning(warn)
                else:
                    port_license = spdx2macports[license.spdx_identifier]
                    self.logger.info(f'Found license {port_license}')
                licenses.append(port_license)
            except KeyError:
                err = f'MacPorts license unknown for {license.spdx_identifier}'
                licenses.append(f'unknown  # {err}')
                self.logger.error(err)
                self.logger.info('Please report the error at https://github.com/macports/upt-macports') # noqa
        return ' '.join(licenses)

    def _depends(self, phase):
        return self.upt_pkg.requirements.get(phase, [])

    @property
    def build_depends(self):
        return self._depends('build')

    @property
    def run_depends(self):
        return self._depends('run')

    @property
    def test_depends(self):
        return self._depends('test')

    @property
    def archive_type(self):
        archive_keyword = {
            'gz': 'gz',
            '7z': '7z',
            'bz2': 'bzip2',
            'lzma': 'lzma',
            'tar': 'tar',
            'zip': 'zip',
            'xz': 'xz'
        }
        try:
            archive_name = self.upt_pkg.get_archive(
                self.archive_format).filename
            archive_type = archive_name.split('.')[-1]
            return archive_keyword.get(archive_type, 'unknown')

        except upt.ArchiveUnavailable:
            self.logger.error('Could not determine the type of the source archive') # noqa
            return 'unknown'

    @property
    def homepage(self):
        return self.upt_pkg.homepage

    def _pkgname(self):
        return self._normalized_macports_name(self.upt_pkg.name)


class MacPortsPythonPackage(MacPortsPackage):
    template = 'python.Portfile'
    archive_format = upt.ArchiveType.SOURCE_TARGZ
    category = 'python'

    @staticmethod
    def _normalized_macports_name(name):
        name = name.lower()
        return f'py-{name}'

    def _python_root_name(self):
        pypi_name = self.upt_pkg.get_archive().filename.split('-'+self.upt_pkg.version)[0] # noqa
        if pypi_name != self.upt_pkg.name.lower():
            return pypi_name

    @staticmethod
    def _normalized_macports_folder(name):
        name = name.lower()
        return f'py-{name}'

    def jinja2_reqformat(self, req):
        return f'py${{python.version}}-{req.name.lower()}'

    @property
    def homepage(self):
        homepage = self.upt_pkg.homepage
        if homepage.startswith('http'):
            return homepage
        else:
            return f'https://pypi.org/project/{self.upt_pkg.name}'


class MacPortsPerlPackage(MacPortsPackage):
    template = 'perl.Portfile'
    archive_format = upt.ArchiveType.SOURCE_TARGZ
    category = 'perl'

    @staticmethod
    def _normalized_macports_name(name):
        return name.replace('::', '-')

    @staticmethod
    def _normalized_macports_folder(name):
        name = name.lower().replace('::', '-')
        return f'p5-{name}'

    def jinja2_reqformat(self, req):
        return f'p${{perl5.major}}-{self._normalized_macports_name(req.name).lower()}' # noqa

    def _cpandir(self):
        pkg = self.upt_pkg
        # If no archives detected then we cannot locate dist file
        if not pkg.archives:
            self.logger.warning('No dist file was found')
            return ' # could not locate dist file'

        # We start by checking at usual location
        archive_name = pkg.archives[0].url.split('/')[-1]
        part_name = pkg.name.replace('::', '-').split('-')[0]
        check_url = f'https://cpan.metacpan.org/modules/by-module/{part_name}/{archive_name}' # noqa
        r = requests.head(check_url)
        if r.status_code == 200:
            self.logger.info('Dist file found at usual location')
            return ''
        else:
            # Sometimes if it is not available,
            # then we fallback to alternate location
            # to be verified by the maintainer
            fallback_dist = '/'.join(pkg.archives[0].url.split('id/')[1].split('/')[:-1]) # noqa
            self.logger.info('Dist file was not found at usual location')
            self.logger.info('Using fallback location for dist file')
            return f' ../../authors/id/{fallback_dist}/'

    @property
    def homepage(self):
        homepage = self.upt_pkg.homepage
        portgroup_default = 'metacpan.org/pod'
        if homepage.startswith('http') and portgroup_default not in homepage:
            return homepage
        else:
            return None


class MacPortsRubyPackage(MacPortsPackage):
    template = 'ruby.Portfile'
    archive_format = upt.ArchiveType.RUBYGEM
    category = 'ruby'

    @staticmethod
    def _normalized_macports_name(name):
        return name

    @staticmethod
    def _normalized_macports_folder(name):
        name = name.lower()
        return f'rb-{name}'

    def jinja2_reqformat(self, req):
        return f'rb${{ruby.suffix}}-{req.name.lower()}'

    @property
    def homepage(self):
        homepage = self.upt_pkg.homepage
        if homepage.startswith('http'):
            return homepage
        else:
            return f'https://rubygems.org/gems/{self.upt_pkg.name}'


class DependsUpdater(object):
    valid_types = {
        # upt phase -> macports phase
        'test': 'test',
        'run': 'lib',
        'build': 'build',
    }

    def __init__(self, type_):
        self._in_section = False
        self.type_ = type_
        self._keyword = f'depends_{self.valid_types[self.type_]}-append'
        # We remember the indentation before self._keyword, and the indentation
        # of the following lines, so that no "whitespace changes" are
        # introduced. We also remember what the spacing was after the keyword,
        # since sometimes multiple spaces are used for pretty printing.
        self._first_line_indent = ''
        self._next_lines_indent = ''
        self._space = ' '

        # The dependencies handled by this Updater. At first, this list is
        # populated through process_line(), so that it holds dependencies
        # already in the Portfile. It is then modified by update() and holds
        # the dependencies that are going to be written to the updated
        # Portfile.
        self.deps = []

    @staticmethod
    def _clean_depends_line(line):
        if line.endswith('\n'):
            line = line[:-1]
        if line.endswith('\\'):
            line = line[:-1]
        return line.strip()

    def process_line(self, line):
        matched = False
        finished = False
        m = re.match(f'(\s*){self._keyword}(\s+)(.*\n)', line)  # noqa
        if m:
            self._in_section = True
            self._first_line_indent = m.group(1)
            self._space = m.group(2)
            line = m.group(3)
        if self._in_section:
            matched = True
            m = re.match(r'(\s+)', line)
            if m:
                self._next_lines_indent = m.group(1)
            self.deps.append(self._clean_depends_line(line))
            if not line.endswith('\\\n'):
                self._in_section = False
                finished = True
        return matched, finished

    def update(self, pdiff, reqformat_fn):
        # First, remove old requirements that are no longer required.
        for deleted_req in pdiff.deleted_requirements(self.type_):
            formatted_req = f'port:{reqformat_fn(deleted_req)}'
            try:
                self.deps.remove(formatted_req)
            except ValueError:
                # This particular requirement is no longer marked as needed
                # upstream. Maybe it was never included in the Makefile, which
                # means that trying to remove it may raise this exception.
                pass

        # Then, add new requirements.
        # Some of the new requirements may already be in the Portfile. This
        # happens when upstream failed to properly specify metadata in the old
        # version and fixed everything in the new one:
        #
        # Old upstream metadata: "required: []" (even though 'foo' is needed)
        # New upstream metadata: "required: ['foo']"
        #
        # In this case, upt will consider that 'foo' is a new requirement.
        # Since it was already required in the old version (even though that
        # was not specified in the metadata), the dependency on 'foo' will
        # already be specified in the Portfile. We need to make sure that we do
        # not duplicate this dependency, hence the if condition in the loop.
        for req in pdiff.new_requirements(self.type_):
            formatted_req = f'port:{reqformat_fn(req)}'
            if formatted_req not in self.deps:
                self.deps.append(formatted_req)

    def build_depends_line(self):
        if self._next_lines_indent == '':
            self._next_lines_indent = self._first_line_indent
            self._next_lines_indent += ' ' * len(self._keyword)
            self._next_lines_indent += self._space
        if self.deps:
            new_depends = f'{self._first_line_indent}{self._keyword}'
            if not self.deps[0]:
                new_depends += ' ' * (len(self._space) - 1)
            else:
                new_depends += self._space
            new_depends += ' \\\n'.join([
                dep if i == 0
                else f'{self._next_lines_indent}{dep}'
                for i, dep in enumerate(self.deps)
            ])
            new_depends += '\n'
        else:
            new_depends = ''
        return new_depends


class DependsUpdaterManager(object):
    '''A manager for all our DependsUpdater objects.

    Conceptually, DependsUpdaterManager is an improved "for loop" over the
    various DependsUpdater objects that are needed to update a Portfile. Users
    should use this class instead of using DependsUpdater objects directly.
    '''
    def __init__(self, pdiff, reqformat_fn):
        self._pdiff = pdiff  # A upt.PackageDiff
        self._reqformat_fn = reqformat_fn  # Requirement-formatting function
        self._depends_updaters = [
            DependsUpdater(type_)
            for type_ in DependsUpdater.valid_types.keys()
        ]

    def process_line(self, line):
        '''Processes a line from an existing Portfile.

        Returns two values:
        - the first one is a boolean indicating whether the given line was part
          of a "depends" block;
        - the second one is a string equal to the updated depends block. It is
          returned when the given line was the last one of a depends block;
          otherwise, we return None.

        Example: consider the following three calls, run one after the other.
        Upstream, the build dependencies have gone from "foo and bar" to just
        "bar".

        process_line('version 13.37') -> False, None
        process_line('depends_build-append port:foo \\') -> True, None
        process_line('    port:bar') -> True, 'depends_build-append port:bar')
        '''
        matched = False
        text = None
        for updater in self._depends_updaters:
            matched, finished = updater.process_line(line)
            if matched:
                break
        if matched and finished:
            self._depends_updaters.remove(updater)
            updater.update(self._pdiff, self._reqformat_fn)
            text = updater.build_depends_line()
        return matched, text

    def flush(self):
        '''Return the depends blocks that have not been returned yet.'''
        ret = ''
        for depends_updater in self._depends_updaters:
            depends_updater.update(self._pdiff, self._reqformat_fn)
            depends_line = depends_updater.build_depends_line()
            if depends_line:
                ret += depends_line
        return ret


class MacPortsBackend(upt.Backend):
    def __init__(self):
        self.logger = logging.getLogger('upt')

    name = 'macports'
    pkg_classes = {
        'pypi': MacPortsPythonPackage,
        'cpan': MacPortsPerlPackage,
        'rubygems': MacPortsRubyPackage,
    }

    def create_package(self, upt_pkg, output=None):
        try:
            self.frontend = upt_pkg.frontend
            pkg_cls = self.pkg_classes[upt_pkg.frontend]
        except KeyError:
            raise upt.UnhandledFrontendError(self.name, upt_pkg.frontend)
        packager = pkg_cls()
        packager.create_package(upt_pkg, output)

    def package_versions(self, name):
        try:
            port_name = self.pkg_classes[
                self.frontend]._normalized_macports_folder(name)
        except KeyError:
            raise upt.UnhandledFrontendError(self.name, self.upt_pkg.frontend)

        self.logger.info(f'Checking MacPorts tree for port {port_name}')
        cmd = f'port info --version {port_name}'
        port = subprocess.getoutput(cmd)
        if port.startswith('Error'):
            self.logger.info(f'{port_name} not found in MacPorts tree')
            return []
        elif port.startswith('version'):
            curr_ver = port.split()[1]
            self.logger.info(
                f'Current MacPorts Version for {port_name} is {curr_ver}')
            return [curr_ver]
        elif port.startswith('Warning'):
            self.logger.warning(
                'port definitions are more than two weeks old, '
                'consider updating them by running \'port selfupdate\'.')
            curr_ver = port.split('version: ')[1]
            self.logger.info(
                f'Current MacPorts Version for {port_name} is {curr_ver}')
            return [curr_ver]
        else:
            sys.exit(f'The command "{cmd}" failed. '
                     'Please make sure you have MacPorts installed '
                     'and/or your PATH is set-up correctly.')

    @staticmethod
    def standardize_CPAN_version(version):
        """Parse CPAN version and return a normalized, dotted-decimal form.

        The resulting version is identical to the MacPorts conversion as
        performed by the perl5 PortGroup.
        It is almost the same as the suggested conversion using:
          perl -Mversion -e 'print version->parse("<VERSION>")->normal'
        with the exception of version numbers that do not contain a "dot".

        """
        version_strip = version.lstrip('v')
        version_split = version_strip.split('.')

        # no or more than 1 'dots': no conversion required
        if len(version_split) != 2:
            return version_strip

        # conversion required
        std_version = version_split[0]
        fractional = version_split[1]

        index = 0
        while index < len(fractional) or index < 6:
            sub = fractional[index:index+3]
            if len(sub) < 3:
                sub += '0'*(3-len(sub))
            std_version += '.' + str(int(sub))
            index += 3

        return std_version

    def needs_requirement(self, req, phase):
        if self.frontend == 'cpan' and req.specifier:
            s = SpecifierSet(req.specifier)
            req.specifier = ', '.join(
                [dep.operator +
                 self.standardize_CPAN_version(dep.version) for dep in s])

        return super().needs_requirement(req, phase)

    @staticmethod
    def _update_version(line, old_version, new_version):
        m = re.match(r'^(version|github.setup|ruby.setup)', line)
        if m:
            line = line.replace(old_version, new_version)
        return line

    @staticmethod
    def _update_revision(line):
        m = re.match(r'^revision(\s+)(\d+)\n', line)
        if m:
            line = f'revision{m.group(1)}0\n'
        return line

    @staticmethod
    def _update_archives(line, old_archive, new_archive):
        if old_archive is None or new_archive is None:
            return line

        # Update sha256/rmd160 hashes
        m = re.match(r'^(.*)(sha256|rmd160)(\s+)[0-9a-f]{40,64}(.*)', line,
                     re.DOTALL)
        if m:
            hash_ = getattr(new_archive, m.group(2))
            return f'{m.group(1)}{m.group(2)}{m.group(3)}{hash_}{m.group(4)}'

        # Update archive size
        m = re.match(r'^(.*)size(\s+)\d+(.*)', line, re.DOTALL)
        if m:
            size = new_archive.size
            return f'{m.group(1)}size{m.group(2)}{size}{m.group(3)}'

        # This line had nothing to do with the archives, let's return it as is.
        return line

    def current_version(self, frontend, pkgname, output=None):
        self.frontend = frontend.name
        try:
            return self.package_versions(pkgname)[0]
        except:  # noqa
            return super().current_version(frontend, pkgname, output=output)

    def _update_portfile_content(self, portfile_handle, pdiff):
        macports_pkg = self.pkg_classes[self.frontend]()
        try:
            archive_format = macports_pkg.archive_format
            old_archive = pdiff.old.get_archive(archive_format)
            new_archive = pdiff.new.get_archive(archive_format)
        except upt.ArchiveUnavailable:
            old_archive = None
            new_archive = None

        dum = DependsUpdaterManager(pdiff, macports_pkg.jinja2_reqformat)
        new_lines = []
        for line in portfile_handle:
            # Update depends_{build,lib,test}-append
            in_depends_block, new_depends_block = dum.process_line(line)
            if in_depends_block:
                # This line is part of a depends block.
                if new_depends_block:
                    # If we get here, this line was the last one of the
                    # depends block, and process_line() returned the
                    # updated depends block.
                    line = new_depends_block
                else:
                    # We are not at the end of the current depends block
                    # yet, so we must keep looping and refrain from
                    # appending anything to new_lines yet.
                    continue  # pragma: nocover
            else:
                # We make the assumption that only one of the following
                # function calls will affect the current line.
                line = self._update_version(line,
                                            pdiff.old_version,
                                            pdiff.new_version)
                line = self._update_revision(line)
                line = self._update_archives(line, old_archive,
                                             new_archive)
            # We make sure to remember the updated line (which may actually
            # be multiple lines concatenated into a single string if we
            # were parsing a block of lines).
            new_lines.append(line)

        # If a "depends" line was not used in the original Portfile, the
        # corresponding DependsUpdater object will not have been used by
        # now, even though there may be new dependencies for the project.
        # Let's use this DependsUpdater object here and try to insert the
        # dependencies as cleanly as possible.
        depends_line = dum.flush()
        if depends_line:
            new_lines.append('#TODO: Move this\n')
            new_lines.append(depends_line)
        return ''.join(new_lines)

    def update_package(self, pdiff, output=None):
        macports_pkg = self.pkg_classes[self.frontend]()

        # TODO: This is basically the same code as the one found in
        # MacPortsPackage._create_output_directories(). It would be nice not to
        # repeat ourselves.
        folder_name = macports_pkg._normalized_macports_folder(pdiff.new.name)
        output_dir = os.path.join(macports_pkg.category, folder_name)
        portfile_path = f'{output_dir}/Portfile'

        with open(portfile_path, 'r+') as f:
            new_portfile_content = self._update_portfile_content(f, pdiff)
            f.seek(0)
            f.write(new_portfile_content)
            f.truncate()
