import numpy as np
import pytest
from shapely.geometry import LineString,Point,mapping,box
from app.terrain import terrain_screen
from app.geo import detect_changes
from app.models import AnalysisRequest
from app.impact import analyze_impacts
from app.geo import Grid
from rasterio.transform import from_origin


@pytest.fixture
def grid():
    return Grid('EPSG:32645',from_origin(400000,3100700,30,30),30,30,30)


def test_physical_terrain_directions_distinguish_layover_and_shadow():
    size=15;theta=np.full((size,size),40.);east=np.full((size,size),-1.);north=np.zeros((size,size))
    flat=np.zeros((size,size))
    _,flags=terrain_screen(flat,30,theta,east,north)
    assert not flags.any()
    foreslope=np.tile(np.arange(size)*30.,(size,1))
    _,flags=terrain_screen(foreslope,30,theta,east,north)
    assert (flags[3:-3,3:-3]&2).all()
    backslope=-foreslope*2
    _,flags=terrain_screen(backslope,30,theta,east,north)
    assert (flags[3:-3,3:-3]&1).all()


def test_distant_ridge_can_occlude_a_locally_flat_target():
    dem=np.zeros((15,40));dem[:,10]=1000
    theta=np.full(dem.shape,40.);east=np.full(dem.shape,-1.);north=np.zeros(dem.shape)
    local,flags=terrain_screen(dem,30,theta,east,north)
    assert local[7,25]==pytest.approx(40.)
    assert flags[7,25]&4
    assert not flags[7,39]&4


def test_clear_optical_debris_remains_eligible_in_steep_radar_shadow(grid):
    radar=np.stack([np.full((30,30),.1),np.ones((30,30)),np.ones((30,30))])
    pre=np.stack([np.full((30,30),v) for v in [.15,.1,.5,.25,4,1]])
    post=np.stack([np.full((30,30),v) for v in [.15,.22,.1,.4,4,1]])
    dem=np.tile(np.arange(30)*60.,(30,1))
    params=AnalysisRequest(water_model='threshold')
    result=detect_changes(radar,radar,grid,params,dem,pre,post)
    assert result['debris'][15,15]
    assert not result['radar_valid'].any()
    assert not result['water_eligible'].any()
    post[4]=9
    result=detect_changes(radar,radar,grid,params,dem,pre,post)
    assert not result['debris'].any()


def collection(grid,items):
    return {'type':'FeatureCollection','features':[{'type':'Feature','geometry':mapping(grid.geographic(geometry)),'properties':properties} for geometry,properties in items]}


def test_directed_road_access_cannot_travel_against_oneway(grid):
    x,y=400000,3100000
    osm=collection(grid,[(LineString([(x,y),(x+600,y)]),{'highway':'primary','oneway':'yes'}),
        (Point(x+550,y),{'place':'village','name':'Village'}),(Point(x+50,y),{'amenity':'hospital','name':'Hospital'})])
    layers,_=analyze_impacts(osm,{'type':'FeatureCollection','features':[]},grid)
    assert layers['settlements']['features'][0]['properties']['status']=='baseline_disconnected'


def test_unobserved_route_is_uncertain_instead_of_confirmed_connected(grid):
    x,y=400000,3100000
    osm=collection(grid,[(LineString([(x,y),(x+600,y)]),{'highway':'primary'}),
        (Point(x+550,y),{'place':'village','name':'Village'}),(Point(x+50,y),{'amenity':'hospital','name':'Hospital'})])
    layers,stats=analyze_impacts(osm,{'type':'FeatureCollection','features':[]},grid,assessment_mask=np.zeros((30,30),dtype=bool))
    assert layers['settlements']['features'][0]['properties']['status']=='uncertain_access'
    assert stats['settlements_access_uncertain']==1
    assert stats['settlements_cut_off']==0


def test_overpass_interior_shared_coordinate_is_not_a_junction(grid):
    x,y=400300,3100300
    osm=collection(grid,[(LineString([(x-200,y),(x,y),(x+200,y)]),{'highway':'primary','bridge':'yes','layer':'1'}),
        (LineString([(x,y-200),(x,y),(x,y+200)]),{'highway':'primary','layer':'0'}),
        (Point(x-180,y),{'place':'village','name':'Village'}),(Point(x,y+180),{'amenity':'hospital','name':'Hospital'})])
    layers,_=analyze_impacts(osm,{'type':'FeatureCollection','features':[]},grid)
    assert layers['settlements']['features'][0]['properties']['status']=='baseline_disconnected'


def test_boundary_touch_alone_does_not_block_a_road(grid):
    x,y=400000,3100000
    osm=collection(grid,[(LineString([(x,y),(x+600,y)]),{'highway':'primary'}),
        (Point(x+550,y),{'place':'village','name':'Village'}),(Point(x+50,y),{'amenity':'hospital','name':'Hospital'})])
    # Use one corner at the road, with the polygon extending away from it.
    from shapely.geometry import Polygon
    extent=collection(grid,[(Polygon([(x+300,y),(x+400,y+100),(x+200,y+100)]),{'kind':'water'})])
    layers,stats=analyze_impacts(osm,extent,grid)
    assert stats['settlements_cut_off']==0
    assert layers['settlements']['features'][0]['properties']['status']=='connected'
