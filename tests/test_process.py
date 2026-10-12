from datetime import datetime, timezone
import json

import httpx
import numpy as np
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from app.geo import Grid
from app.models import Scene
from app import providers


def test_live_radar_request_records_terrain_processing_and_pass_window(monkeypatch,tmp_path):
    grid=Grid("EPSG:32645",from_origin(400000,3101000,30,30),8,8,30)
    data=np.stack([np.full((8,8),.1),np.ones((8,8)),np.zeros((8,8))]).astype("float32")
    with MemoryFile() as memory:
        with memory.open(driver="GTiff",height=8,width=8,count=3,dtype="float32",crs=grid.crs,transform=grid.transform) as dataset:
            dataset.write(data)
        tiff=memory.read()
    calls=[]
    def respond(request):
        if str(request.url)==providers.TOKEN_URL:
            return httpx.Response(200,json={"access_token":"synthetic-test-token"})
        calls.append(json.loads(request.content))
        return httpx.Response(200,content=tiff,headers={"Content-Type":"image/tiff"})
    original=httpx.Client
    monkeypatch.setattr(providers.httpx,"Client",lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    monkeypatch.setenv("CDSE_CLIENT_ID","synthetic-test-client")
    monkeypatch.setenv("CDSE_CLIENT_SECRET","synthetic-test-secret")
    provider=providers.CopernicusProvider(grid,tmp_path)
    scene=Scene(id="SYNTHETIC",acquired=datetime(2026,8,20,tzinfo=timezone.utc),relative_orbit=85,direction="ascending")
    raster=provider.process("sentinel-1-grd","s1-before",scene)
    np.testing.assert_array_equal(raster,data)
    request=calls[0]["input"]["data"][0]
    assert request["processing"]["backCoeff"] == "GAMMA0_TERRAIN"
    assert request["processing"]["orthorectify"] is True
    assert request["processing"]["demInstance"] == "COPERNICUS_30"
    assert request["dataFilter"]["orbitDirection"] == "ASCENDING"
    window=request["dataFilter"]["timeRange"]
    assert (datetime.fromisoformat(window["to"])-datetime.fromisoformat(window["from"])).total_seconds() == 90
    assert (tmp_path/"s1-before.tif").exists()
