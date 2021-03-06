import os
import re
import urllib

import xml.etree.ElementTree as ET

import yaml

__location__ = os.path.realpath(
    os.path.join(os.getcwd(), os.path.dirname(__file__)))

def metadata_sort_key(name):
    sections = []
    for section in re.split('[.|-]', name):
        sections.append(metadata_sort_key_section(section))

    key = '_'.join(sections)
    key = key.replace('_','Z')

    return key

def metadata_sort_key_section(name):
    prefix = '5'
    key = name

    # Sort namespace prefixed names last
    base_name = name
    if base_name.endswith('__c'):
        base_name = base_name[:-3]
    if base_name.find('__') != -1:
        prefix = '8'

    key = prefix + name 
    return key

class MetadataParserMissingError(Exception):
    pass

class PackageXmlGenerator(object):
    def __init__(self, directory, api_version, package_name=None, managed=None, delete=None, install_class=None, uninstall_class=None):
        f_metadata_map = open(__location__ + '/metadata_map.yml', 'r')
        self.metadata_map = yaml.load(f_metadata_map)
        self.directory = directory
        self.api_version = api_version
        self.package_name = package_name
        self.managed = managed
        self.delete = delete
        self.install_class = install_class
        self.uninstall_class = uninstall_class
        self.types = []

    def __call__(self):
        self.parse_types()
        return self.render_xml()

    def parse_types(self):
        for item in os.listdir(self.directory):
            if item == 'package.xml':
                continue
            if not os.path.isdir(self.directory + '/' + item):
                continue
            if item.startswith('.'):
                continue
            config = self.metadata_map.get(item)
            if not config:
                raise MetadataParserMissingError('No parser configuration found for subdirectory %s' % item)

            for parser_config in config:
                if parser_config.get('options'):
                    parser = globals()[parser_config['class']](
                        parser_config['type'],                # Metadata Type
                        self.directory + '/' + item,          # Directory
                        parser_config.get('extension', ''),   # Extension
                        self.delete,                          # Parse for deletion?
                        **parser_config.get('options', {})    # Extra kwargs
                    )
                else:
                    parser = globals()[parser_config['class']](
                        parser_config['type'],                # Metadata Type
                        self.directory + '/' + item,          # Directory
                        parser_config.get('extension', ''),   # Extension
                        self.delete,                          # Parse for deletion?
                    )

                self.types.append(parser)

    def render_xml(self):
        lines = []

        # Print header
        lines.append(u'<?xml version="1.0" encoding="UTF-8"?>')
        lines.append(u'<Package xmlns="http://soap.sforce.com/2006/04/metadata">')
        if self.package_name:
            package_name_encoded = urllib.quote(self.package_name, safe=' ')
            lines.append(
                u'    <fullName>{0}</fullName>'.format(package_name_encoded)
            )
   
        # Print types sections 
        self.types.sort(key=lambda x: x.metadata_type.upper())
        for parser in self.types:
            type_xml = parser()
            if type_xml:
                lines.extend(type_xml)

        # Print footer
        lines.append(u'    <version>{0}</version>'.format(self.api_version))
        lines.append(u'</Package>')

        return u'\n'.join(lines)

class BaseMetadataParser(object):

    def __init__(self, metadata_type, directory, extension, delete):
        self.metadata_type = metadata_type
        self.directory = directory
        self.extension = extension
        self.delete = delete
        self.members = []

        if self.delete:
            self.delete_excludes = self.get_delete_excludes()

    def __call__(self):
        self.parse_items()
        return self.render_xml()

    def get_delete_excludes(self):
        f = open(__location__ + '/../../build/whitelists/metadata.txt', 'r')
        excludes = []
        for line in f:
            excludes.append(line.strip())
        return excludes

    def parse_items(self):
        # Loop through items
        for item in os.listdir(self.directory):
            if self.extension and not item.endswith('.' + self.extension):
                continue

            if item.endswith('-meta.xml'):
                continue

            if self.check_delete_excludes(item):
                continue
            
            self.parse_item(item)

    def check_delete_excludes(self, item):
        if not self.delete:
            return False
        if item in self.delete_excludes:
            return True
        return False

    def parse_item(self, item):
        members = self._parse_item(item)
        if members:
            self.members.extend(members)

    def _parse_item(self, item):
        "Receives a file or directory name and returns a list of members"
        raise NotImplemented("Subclasses should implement their parser here")

    def strip_extension(self, filename):
        return '.'.join(filename.split('.')[:-1])

    def render_xml(self):
        output = []
        if not self.members:
            return
        output.append(u'    <types>')
        self.members.sort(key=lambda x: metadata_sort_key(x))
        for member in self.members:
            output.append(u'        <members>{0}</members>'.format(member))
        output.append(u'        <name>{0}</name>'.format(self.metadata_type)) 
        output.append(u'    </types>')
        return output
        

class MetadataFilenameParser(BaseMetadataParser):
    
    def _parse_item(self, item):
        return [self.strip_extension(item)]

class MetadataFolderParser(BaseMetadataParser):
    
    def _parse_item(self, item):
        members = []
        path = self.directory + '/' + item

        # Skip non-directories
        if not os.path.isdir(path):
            return members

        # Add the member if it is not namespaced
        if item.find('__') == -1:
            members.append(item)
    
        for subitem in os.listdir(path):
            if subitem.endswith('-meta.xml'):
                continue
            submembers = self._parse_subitem(item, subitem)
            members.extend(submembers)

        return members

    def check_delete_excludes(self, item):
        return False

    def _parse_subitem(self, item, subitem):
        return [item + '/' + self.strip_extension(subitem)]

class MissingNameElementError(Exception):
    pass

class ParserConfigurationError(Exception):
    pass

class MetadataXmlElementParser(BaseMetadataParser):

    namespaces = {'sf': 'http://soap.sforce.com/2006/04/metadata'}

    def __init__(self, metadata_type, directory, extension, delete, item_xpath=None, name_xpath=None):
        super(MetadataXmlElementParser, self).__init__(metadata_type, directory, extension, delete)
        if not item_xpath:
            raise ParserConfigurationError('You must provide a value for item_xpath')
        self.item_xpath = item_xpath
        if not name_xpath:
            name_xpath = './sf:fullName'
        self.name_xpath = name_xpath

    def _parse_item(self, item):
        root = ET.parse(self.directory + '/' + item)
        members = []

        parent = self.strip_extension(item)

        for item in self.get_item_elements(root):
            members.append(self.get_item_name(item, parent))

        return members
       
    def check_delete_excludes(self, item):
        return False

    def get_item_elements(self, root): 
        return root.findall(self.item_xpath, self.namespaces)

    def get_name_elements(self, item):
        return item.findall(self.name_xpath, self.namespaces)

    def get_item_name(self, item, parent):
        """ Returns the value of the first name element found inside of element """
        names = self.get_name_elements(item)
        if not names:
            raise MissingNameElementError

        name = names[0].text
        prefix = self.item_name_prefix(parent)
        if prefix:
            name = prefix + name
            
        return name

    def item_name_prefix(self, parent):
        return parent + '.'

# TYPE SPECIFIC PARSERS

class CustomLabelsParser(MetadataXmlElementParser):
    def item_name_prefix(self, parent):
        return ''

class CustomObjectParser(MetadataFilenameParser):
    def _parse_item(self, item):
        members = []

        # Skip namespaced custom objects
        if len(item.split('__')) > 2:
            return members

        # Skip standard objects
        if not item.endswith('__c.object'):
            return members

        members.append(self.strip_extension(item))
        return members
    
class RecordTypeParser(MetadataXmlElementParser):
    def check_delete_excludes(self, item):
        if self.delete:
            return True

class BusinessProcessParser(MetadataXmlElementParser):
    def check_delete_excludes(self, item):
        if self.delete:
            return True

class AuraBundleParser(MetadataFilenameParser):
    def _parse_item(self, item):
        return [item]

class DocumentParser(MetadataFolderParser):        
    def _parse_subitem(self, item, subitem):
        return [item + '/' + subitem]
