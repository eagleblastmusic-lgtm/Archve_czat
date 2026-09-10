"""Isolated protocol, disk cache and local numbered-video checks."""
import sys,tempfile,time,threading,subprocess,json
from pathlib import Path
from unittest.mock import patch,MagicMock
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests,config
with patch.object(requests.Session,'request',side_effect=AssertionError('External HTTP disabled')),patch.object(config,'get_archivebate_credentials',return_value=('','')):
    import main,storyboard_service as story
    from fastapi.testclient import TestClient
    from PIL import Image,ImageDraw
    client=TestClient(main.app)
    for status,body,extra in [(200,b'abcdefgh',{}),(206,b'cdef',{'Content-Range':'bytes 2-5/8'}),(416,b'',{'Content-Range':'bytes */8'})]:
        upstream=MagicMock(status_code=status,headers={'Content-Type':'video/mp4','Content-Length':str(len(body)),'Accept-Ranges':'bytes',**extra})
        upstream.iter_content.return_value=iter([body])
        with patch.object(main,'_validated_session_get',return_value=upstream),patch.object(main,'is_safe_remote_url',return_value=True):
            response=client.get('/api/video/stream',params={'url':'https://fixture.invalid/video'},headers={'Range':'bytes=2-5'})
            assert response.status_code==status and response.content==body,(status,response.text)
            for key,value in extra.items():assert response.headers[key]==value
            upstream.close.assert_called()
    with patch.object(main,'get_storyboard_status',return_value={'status':'ready','quality':'full','created_at':1}),patch.object(main,'_fetch_details_singleflight',side_effect=AssertionError('Unexpected resolver')):
        assert client.get('/api/storyboard?id=fixture&duration=10').status_code==200
    page=client.get('/').text
    import re
    asset=re.search(r'/static/app.js\?v=[a-f0-9]+',page).group()
    assert 'immutable' in client.get(asset).headers['cache-control']
    cache={'data':{'id':'fixture','direct_url':'old'},'mtime':time.time()};calls=[]
    def fetch(ident):
        calls.append(ident);time.sleep(.03);cache.update(data={'id':ident,'direct_url':'new'},mtime=time.time());return cache['data']
    with patch.object(main,'read_json_cache',side_effect=lambda p:(cache['data'],cache['mtime'])),patch.object(main,'_fetch_and_cache_details',side_effect=fetch):
        with ThreadPoolExecutor(10) as pool:
            values=list(pool.map(lambda _:main._fetch_details_singleflight('fixture',force=True,rejected_url='old'),range(10)))
        assert len(calls)==1 and all(v['direct_url']=='new' for v in values)
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp,patch.object(story,'STORYBOARD_CACHE_DIR',tmp):
        root=Path(tmp)
        for i in range(10):
            image=Image.new('RGB',(160,90),(i*24,30,240-i*20));ImageDraw.Draw(image).text((50,30),str(i),fill='white',font_size=30);image.save(root/f'{i:02}.png')
        video=root/'numbered.mp4'
        subprocess.run([story.imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-framerate','1','-i',str(root/'%02d.png'),'-c:v','libx264','-pix_fmt','yuv420p','-y',str(video)],check=True)
        extract=story._extract_one
        def selective(ffmpeg,url,target,path,timeout):
            if path.name in ('frame_000.jpg','frame_003.jpg'):return False
            return extract(ffmpeg,url,target,path,timeout)
        with patch.object(story,'_extract_one',side_effect=selective):
            manifest=story._build_variant('fixture',10,str(video),'quick')
        assert manifest['selected_indices'][0]!=0 and manifest['selected_indices'][3]!=3
        for i,chosen in enumerate(manifest['selected_indices']):assert manifest['times'][i]==manifest['requested_times'][chosen]
        assert story.get_status('fixture',10)['status']=='ready'
    calls=[]
    def build(ident,duration,url,quality):
        calls.append((ident,quality));time.sleep(.03);return {'quality':quality}
    with patch.object(story,'_cached_variant',return_value=None),patch.object(story,'_build_variant',side_effect=build):
        story.start('no-demand-fixture',10,'local')
        story._jobs.join()
        assert calls == [], calls
        story.demand('concurrent-fixture','test-consumer')
        with ThreadPoolExecutor(10) as pool:list(pool.map(lambda _:story.start('concurrent-fixture',10,'local'),range(10)))
        story._jobs.join()
        assert calls==[('concurrent-fixture','quick'),('concurrent-fixture','full')],calls
        story.demand('concurrent-fixture','test-consumer',active=False)
print('PASS: Range 200/206/416 exact bytes; ready-before-resolver; asset hashes; 10-way refresh; local numbered storyboard substitutions; single generator')
