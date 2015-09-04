import datetime
import dateutil.parser
import girder_client
import lxml.html
import requests
import stampfile

# Root path for scraping SVS files
URLBASE = 'https://tcga-data.nci.nih.gov/tcgafiles/ftp_auth/distro_ftpusers/anonymous/tumor/lgg/bcr/nationwidechildrens.org/tissue_images/'

_slideTypes = {
    'DX': 'Diagnostic',
    'TS': 'Frozen'
}

class MetadataParseException(Exception):
    pass


def findSvsFiles(url):
    """
    Given a URL to an apache mod_autoindex directory listing, recursively
    scrapes the listing for .svs files. This is a generator that yields each
    such file found in the listing as a tuple whose first element is the URL
    and whose second element is its modified time as reported by the server.
    """
    doc = lxml.html.fromstring(requests.get(url + '?F=2').text)
    rows = doc.xpath('.//table//tr')

    for row in rows:
        name = row.xpath('.//td[2]/a/text()')

        if not name:  # F=2 gives us some header rows that only contain <th>
            continue

        name = name[0].strip()

        if name.endswith('/'):  # subdirectory
            for svs in findSvsFiles(url + name):
                yield svs
        elif name.endswith('.svs'):  # svs file
            mtime = row.xpath('.//td[3]/text()')[0].strip()
            yield (url + name, mtime)


def extractMetadataFromUrl(url):
    """
    Given a full path to an SVS file, we extract all relevant metadata that is
    represented in the filename and its absolute path.
    """
    basename = url.split('/')[-1]

    try:
        barcode, uuid, _ = basename.split('.')
        barcodeParts = barcode.split('-')

        if barcodeParts[0] != 'TCGA':
            raise MetadataParseException('First barcode token should be "TCGA"')

        metadata = {
            'OriginalUrl': url,
            'FullBarcode': barcode,
            'TSS': barcodeParts[1],
            'Participant': barcodeParts[2],
            'Sample': barcodeParts[3][:2],
            'Vial': barcodeParts[3][2:],
            'Portion': barcodeParts[4][:2],
            #'Analyte': barcodeParts[4][2:],
            'Slide': barcodeParts[5][:2],
            'SlideOrder': barcodeParts[5][2:]
        }

        metadata['SlideType'] = _slideTypes.get(metadata['Slide'], 'Unknown')

        return {
            'basename': basename,
            'folderName': '-'.join(barcodeParts[1:3]),
            'itemName': uuid,
            'metadata': metadata
        }
    except Exception:
        print('!!! Malformed filename, could not parse: ' + basename)
        raise


def createGirderData(client, parent, parentType, info, url):
    folder = client.load_or_create_folder(
        info['folderName'], parent['_id'], parentType)

    item = client.load_or_create_item(info['itemName'], folder['_id'])

    client.addMetadataToItem(item['_id'], info['metadata'])


def ingest(client, importUrl, parent, parentType, verbose=False):
    stamps = stampfile.readStamps()
    dateThreshold = stamps.get(importUrl)

    if dateThreshold:
        print('--- Limiting to files newer than %s.' % str(dateThreshold))

    maxDate = dateThreshold or datetime.datetime.min

    for url, mtime in findSvsFiles(importUrl):
        date = dateutil.parser.parse(mtime)
        maxDate = max(maxDate, date)

        if dateThreshold and date <= dateThreshold:
            if verbose:
                print('--- Skipping %s due to mtime.' % url)
            continue

        info = extractMetadataFromUrl(url)
        createGirderData(client, parent, parentType, info, url)

    stamps[importUrl] = maxDate
    stampfile.writeStamps(stamps)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Import TCGA data into a girder server.')
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', default='8080')
    parser.add_argument('--username')
    parser.add_argument('--password')
    parser.add_argument('--scheme')
    parser.add_argument('--api-root')
    parser.add_argument('--parent-type', default='collection',
                        help='(default: collection)')
    parser.add_argument('--parent-id', required=True)
    parser.add_argument('-v', '--verbose', action='store_true')

    args = parser.parse_args()

    client = girder_client.GirderClient(
        host=args.host, port=int(args.port), apiRoot=args.api_root, scheme=args.scheme)
    client.authenticate(args.username, args.password, interactive=(args.password is None))

    parent = client.getResource(args.parent_type, args.parent_id)

    ingest(client, URLBASE, parent, args.parent_type, verbose=args.verbose)
