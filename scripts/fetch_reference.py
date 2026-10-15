"""Download an explicitly selected EMS activation to its own evaluation directory."""
import argparse
import json
from pathlib import Path
import sys
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import httpx
import pyogrio
from shapely import from_wkt,make_valid
from shapely.geometry import mapping
from shapely.ops import unary_union

from app.cdse_runtime import request_with_retry
from app.reference import activation_code,reference_directory
from app.settings import ACQUISITION
from app.storage import download,save_json,sha256


def fetch_reference(activation,output=None):
    code=activation_code(activation)
    folder=reference_directory(code,output)
    with httpx.Client(timeout=ACQUISITION.reference_timeout_seconds,follow_redirects=True) as client:
        response=request_with_retry(client,'GET',ACQUISITION.ems_activation_url,
            params={'code':code},retry_transport_errors=True)
        response.raise_for_status()
        payload=response.json()
        source_url=str(response.request.url)
    returned=payload.get('results') if isinstance(payload,dict) else None
    if not isinstance(returned,list) or len(returned)!=1 or payload.get('count')!=1 or returned[0].get('code')!=code:
        raise ValueError('The official API did not return the requested activation')
    # Validate both remote identity and existing local identity before writing.
    reference_directory(code,folder)
    folder.mkdir(parents=True,exist_ok=True)
    save_json(folder/'activation.json',payload)
    records,references,coverage=[],[],[]
    for aoi in payload['results'][0]['aois']:
        products=[p for p in aoi['products'] if p.get('downloadPath') and p['version']['statusCode']=='F']
        for product in products:
            url=product['downloadPath']+'?type=vectors'
            zip_path=folder/f"{product['id']}-vectors.zip"
            print(f"Downloading {code} AOI {aoi['number']} product {product['id']} vectors (evaluation only)",flush=True)
            download(url,zip_path,limit=256*1024*1024)
            product_folder=folder/str(product['id'])
            product_folder.mkdir(exist_ok=True)
            with ZipFile(zip_path) as archive:
                for member in archive.infolist():
                    target=product_folder/member.filename
                    if not target.resolve().is_relative_to(product_folder.resolve()) or member.file_size>128*1024*1024:
                        raise ValueError('Unsafe or oversized member in the official vector archive')
                    if not member.is_dir():
                        target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(archive.read(member))
            layers,not_analysed,product_reference=[],[],[]
            for path in sorted(product_folder.rglob('*.shp')):
                frame=pyogrio.read_dataframe(path).to_crs('EPSG:4326')
                layers.append({'file':str(path.relative_to(folder)),'features':len(frame),'columns':list(frame.columns)})
                if 'notanalysed' in path.stem.lower():
                    not_analysed.extend(make_valid(g) for g in frame.geometry if g is not None and not g.is_empty)
                if 'observedeventa' in path.stem.lower():
                    for feature in json.loads(frame.to_json())['features']:
                        feature['properties'].update(activation=code,aoi=aoi['number'],product_id=product['id'],source_layer=path.name)
                        product_reference.append(feature)
            records.append({'product_id':product['id'],'aoi':aoi['number'],'name':aoi['name'],'type':product['type'],
                'monitoring':product['monitoring'],'download_url':url,'sha256':sha256(zip_path),
                'version':product['version'],'images':product['images'],'layers':layers})
            if not product['monitoring'] and product_reference:
                references.extend(product_reference)
                reviewed=make_valid(from_wkt(product['extent'])).difference(unary_union(not_analysed))
                coverage.append({'type':'Feature','geometry':mapping(reviewed),'properties':{
                    'activation':code,'aoi':aoi['number'],'product_id':product['id'],
                    'method':'Official product extent minus official not-analysed polygons'}})
    save_json(folder/'reference-extent.geojson',{'type':'FeatureCollection','features':references})
    save_json(folder/'reviewed-coverage.geojson',{'type':'FeatureCollection','features':coverage})
    save_json(folder/'manifest.json',{'activation':code,'source_url':source_url,
        'role':'evaluation only; this directory is never read by acquisition or prediction','products':records,
        'reference_classes':'All polygons in observedEventA; inspect class attributes before interpreting combined water/debris scores',
        'attribution':'European Union, Copernicus Emergency Management Service data'})
    return {'activation':code,'output':str(folder),'products':len(records),'reference_polygons':len(references)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activation',required=True,help='Explicit EMS activation code')
    parser.add_argument('--output',type=Path,help='Defaults to data/reference_only/ACTIVATION; another activation cannot reuse it')
    args=parser.parse_args()
    try:result=fetch_reference(args.activation,args.output)
    except (ValueError,OSError,httpx.HTTPError) as error:parser.exit(2,f'Reference download failed: {error}\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
