#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Generator - a Inkscape extension to generate end-use files from a model

Initiator:  Aurélio A. Heckert (Bash version, up to Version 0.4)
Contributor: Gaël Ecorchard (Python version)

The MIT License (MIT)

Copyright (c) 2014 Gaël Ecorchard

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

Release notes:
    - version 0.5, 2014-11: complete rewrite in Python of the original Bash
                            extension
                 * added support for csv data with commas
                 * added support for csv data with xml special characters
                 * added support for layer visibility change based on variables
                 * temporarily removed jpg support because Inkscape cannot
                   convert to jpg from the command line.
                 * temporarily removed the gui functionalities provided by
                   zenity.
'''
import os
import tempfile
import csv
import re
import shutil
import StringIO
from lxml import etree
from xml.sax.saxutils import escape
import inkex
from inkex import errormsg
from gettext import gettext as _

_use_rsvg = False


class Generator(inkex.Effect):
    def __init__(self, *args, **kwargs):
        inkex.Effect.__init__(self, *args, **kwargs)
        self.OptionParser.add_option('--tab')
        self.OptionParser.add_option('--preview',
                                     action='store', type='string',
                                     dest='preview', default='false',
                                     help='Preview')
        self.OptionParser.add_option('--extra-vars',
                                     action='store', type='string',
                                     dest='extra_vars', default='',
                                     help='Output format')
        self.OptionParser.add_option('--format',
                                     action='store', type='string',
                                     dest='format', default='PDF',
                                     help='Output format')
        self.OptionParser.add_option('--dpi',
                                     action='store', type='string',
                                     dest='dpi', default='90',
                                     help='dpi (resolution for png and jpg)')
        self.OptionParser.add_option('-t', '--var-type',
                                     action='store', type='string',
                                     dest='var_type', default='name',
                                     help=('Replace variables by ' +
                                           'column number ' +
                                           '(number) or column name (name)'))
        self.OptionParser.add_option('-d', '--data-file',
                                     action='store', type='string',
                                     dest='datafile', default='data.csv',
                                     help='The csv file')
        self.OptionParser.add_option('-o', '--output',
                                     action='store', type='string',
                                     dest='output', default='%VAR_1.pdf',
                                     help='Output pattern')
        self.header = None
        self.data = None
        self.tmpdir = tempfile.mkdtemp(prefix='ink-generator')
        # svgouts is a dict {row_as_list: tmp_svg_output_file}
        self.svgouts = {}

    def effect(self):
        """Do the work"""
        self.options.format = self.options.format.lower()
        self.handle_csv()
        if self.options.var_type == 'name':
            self.create_svg_name()
        else:
            self.create_svg_number()
        self.export()
        if self.options.preview.lower() == 'true':
            self.show_preview()
        self.clean()

    def handle_csv(self):
        """Read data from the csv file and store the rows into self.data"""
        try:
            reader = csv.reader(open(self.options.datafile, 'r'))
        except IOError:
            errormsg(_('Cannot read "{}"'.format(self.options.datafile)))
            raise Exception(_('Cannot read "{}"'.format(self.options.datafile)))
        if self.options.var_type == 'name':
            try:
                self.header = reader.next()
            except StopIteration:
                errormsg(_('Data file "{}" contains no data'.format(
                    self.options.datafile)))
                raise Exception(_('Data file "{}" contains no data'.format(
                    self.options.datafile)))
        self.data = []
        for row in reader:
            self.data.append(row)

    def create_svg_number(self):
        """Create a header, read each line and fill self.svgouts"""
        self.header = [str(i) for i in range(len(self.data[0]))]
        self.create_svg_name()

    def create_svg_name(self):
        """Read each line and fill self.svgouts"""
        for l in self.data:
            d = self.get_line_desc(l)
            self.svgouts[tuple(l)] = self.create_svg(d)

    def create_svg(self, name_dict):
        """Writes out a modified svg"""
        s = StringIO.StringIO()
        for svg_line in open(self.svg_file, 'r').readlines():
            # Modify the line to handle replacements from extension GUI
            svg_line = self.expand_extra_vars(svg_line, name_dict)
            # Modify the line to handle variables in svg file
            svg_line = self.expand_vars(svg_line, name_dict)
            s.write(svg_line)
        # Modify the svg to include or exclude groups
        root = etree.fromstring(s.getvalue())
        self.filter_layers(root, name_dict)
        svgout = self.get_svgout()
        try:
            f = open(svgout, 'w')
            f.write(etree.tostring(root,
                                   encoding='utf-8',
                                   xml_declaration=True))
        except IOError:
            errormsg(_('Cannot open "' + svgout + '" for writing'))
        finally:
            f.close()
        s.close()
        return svgout

    def get_svgout(self):
        """Return the name of a temporary svg file"""
        return tempfile.mktemp(dir=self.tmpdir, suffix='.svg')

    def get_line_desc(self, line):
        """Return the current csv line as dict with csv headers as keys"""
        return dict(zip(self.header, line))

    def get_output(self, name_dict):
        """Return the name of the output file for a csv entry"""
        return self.expand_vars(self.options.output, name_dict)

    def expand_extra_vars(self, line, name_dict):
        """Replace extra replacement values with the content from a csv entry"""
        if not self.options.extra_vars:
            return line
        replacement_strings = self.options.extra_vars.split('|')
        for t in replacement_strings:
            try:
                old_txt, column = t.split('=>')
            except ValueError:
                errormsg(_('Unrecognized replacement string {}'.format(t)))
                raise Exception(_('Unrecognized replacement string {}'.format(
                    t)))
            if line.find(old_txt) < 0:
                # Nothing to be replaced.
                continue
            try:
                new_txt = escape(name_dict[column])
            except KeyError:
                if self.options.var_type == 'name':
                    errormsg(_('Wrong column name "{}"'.format(column)))
                    raise Exception(_('Wrong column name "{}"'.format(column)))
                else:
                    errormsg(_('Wrong column number ({})'.format(column)))
                    raise Exception(_('Wrong column number ({})'.format(
                        column)))
            line = line.replace(old_txt, new_txt)
        return line

    def expand_vars(self, line, name_dict):
        """Replace %VAR_???% with the content from a csv entry"""
        if '%' not in line:
            return line
        for k, v in name_dict.iteritems():
            line = line.replace('%VAR_' + k + '%', escape(v))
        return line

    def filter_layers(self, root, name_dict):
        """Return the xml root with filtered layers"""
        for g in root.xpath("//svg:g", namespaces=inkex.NSS):
            attr = inkex.addNS('label', ns='inkscape')
            if attr not in g.attrib:
                # Not a layer, skip.
                continue
            label = g.attrib[attr]
            if '%' not in label:
                # Nothing to be done, skip.
                continue

            # Treat %IF_???% layers
            match = re.match('.*%IF_([^%]*)%', label)
            if match is not None:
                lookup = match.groups()[0]
                try:
                    var = name_dict[lookup]
                except KeyError:
                    errormsg(_('Column "' + lookup + '" not in the csv file'))
                    continue
                if var and (var.lower() not in ('0', 'false', 'no')):
                    # Set group visibility to true.
                    if 'style' in g.attrib:
                        del g.attrib['style']
                    # Include the group.
                    continue
                else:
                    # Remove the group's content.
                    g.clear()

            # Treat %UNLESS_???% layers
            match = re.match('.*%UNLESS_([^%]*)%', label)
            if match is not None:
                lookup = match.groups()[0]
                try:
                    var = name_dict[lookup]
                except KeyError:
                    errormsg(_('Column "' + lookup + '" not in the csv file'))
                    continue
                if not(var) or (var.lower() in ('0', 'false', 'no')):
                    # Set group visibility to true.
                    if 'style' in g.attrib:
                        del g.attrib['style']
                    # Include the group.
                    continue
                else:
                    # Remove the group's content.
                    g.clear()

    def export(self):
        """Writes out all output files"""
        def get_export_cmd(svgfile, fmt, dpi, outfile):
            if _use_rsvg and os.name == 'posix':
                # Deactivated for now because rsvg-convert (v 2.36.4) changes
                # the size in output pdf files for some svg files. It's a pity,
                # rsvg-convert is much faster.
                ret = os.system('rsvg-convert --version 1>/dev/null')
                if ret == 0:
                    return ('rsvg-convert' +
                            ' --dpi-x=' + dpi +
                            ' --dpi-y=' + dpi +
                            ' --format=' + fmt +
                            ' --output="' + outfile + '"' +
                            ' "' + svgfile + '"')
            else:
                return ('inkscape --without-gui ' +
                        '--export-dpi=' + dpi + ' ' +
                        '--export-' + fmt + '="' + outfile + '" '
                        '"' + svgfile + '"')

        for line, svgfile in self.svgouts.iteritems():
            d = self.get_line_desc(line)
            outfile = self.get_output(d)
            if self.options.format == 'jpg':
                # TODO: output a jpg file
                self.options.format = 'png'
                outfile = outfile.replace('jpg', 'png')
            if self.options.format == 'svg':
                try:
                    shutil.move(svgfile, outfile)
                except OSError:
                    errormsg(_('Cannot create "' + outfile + '"'))
            else:
                cmd = get_export_cmd(svgfile,
                                     self.options.format,
                                     self.options.dpi, outfile)
                os.system(cmd)

    def show_preview(self):
        systems = {
            'nt': os.startfile if 'startfile' in dir(os) else None,
            'posix': lambda fname: os.system(
                'xdg-open "{0}"'.format(fname)),
            'os2': lambda fname: os.system(
                'open "{0}"'.format(fname)),
        }
        try:
            line = self.svgouts.keys()[0]
            d = self.get_line_desc(line)
            outfile = self.get_output(d)
            systems[os.name](outfile)
        except:
            errormsg(_('Error open preview file'))

    def clean(self):
        """Delete temporary svg files and directory"""
        if self.options.format != 'svg':
            for svgfile in self.svgouts.itervalues():
                os.remove(svgfile)
        os.rmdir(self.tmpdir)

if __name__ == '__main__':
    e = Generator()
    e.affect()
