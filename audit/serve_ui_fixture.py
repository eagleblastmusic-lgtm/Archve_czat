"""Local UI fixture; no provider requests or user-store writes."""
import sys,io,asyncio,subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests,config
requests.Session.request=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('Provider network disabled in UI fixture'))
with patch.object(config,'get_archivebate_credentials',return_value=('','')):
 import main
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse,FileResponse,Response
from fastapi.staticfiles import StaticFiles
from PIL import Image,ImageDraw
import imageio_ffmpeg
root=Path(__file__).parent/'ui_fixture'
root.mkdir(exist_ok=True)
for i in range(8):
 im=Image.new('RGB',(160,90),(30*i,50,220-20*i));ImageDraw.Draw(im).text((50,30),str(i),fill='white',font_size=30);im.save(root/f'{i:02}.png')
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-framerate','1','-i',str(root/'%02d.png'),'-c:v','libx264','-pix_fmt','yuv420p','-y',str(root/'clip.mp4')],check=True)
sprite=Image.new('RGB',(640,180))
for i in range(8):sprite.paste(Image.open(root/f'{i:02}.png'),((i%4)*160,(i//4)*90))
sprite.save(root/'sprite.jpg')
app=FastAPI()
app.mount('/static',main.NoCacheStaticFiles(directory=main.STATIC_DIR))
@app.get('/')
def index():return main.serve_index()
@app.get('/watch/{ident}')
def watch(ident:str):return main.serve_watch_page(ident)
@app.get('/fixture.jpg')
def thumb():return FileResponse(root/'00.png')
@app.api_route('/api/{path:path}',methods=['GET','POST','DELETE'])
async def api(path:str,request:Request):
 ident=request.query_params.get('id','0')
 cards=[{'id':str(i),'username':f'Fixture{i}','source':'archivebate','duration':'00:08','date':'2026-09-08','poster':'/fixture.jpg','poster_proxy':'/fixture.jpg','thumbnail':'/fixture.jpg','url':f'/watch/{i}'} for i in range(64)]
 if path=='video/stream':return FileResponse(root/'clip.mp4',media_type='video/mp4')
 if path=='video/details':
  await asyncio.sleep(.35 if ident=='0' else .02)
  return {**cards[int(ident)%64],'direct_url':f'/api/video/stream?id={ident}','proxy_stream_url':f'/api/video/stream?id={ident}'}
 if path=='storyboard/image':return FileResponse(root/'sprite.jpg')
 if path=='storyboard':return {'status':'ready','quality':'full','sprite_url':'/api/storyboard/image','frame_count':8,'columns':4,'rows':2,'frame_width':160,'frame_height':90,'times':list(range(8)),'duration':8}
 if path in ('feed','videos'):return {'snapshot_id':'fixture','revision':1,'page':1,'last_page':1,'complete':True,'known_count':64,'count':64,'videos':cards}
 if path=='status':return {'logged_in':False,'favorites_count':0,'history_count':0,'following_count':0,'favorite_authors':[]}
 if path=='tags':return {'tags':[]}
 if path=='blocked_models':return {'blocked_models':[]}
 if path=='account/history/record':return {'total_history':1}
 if path.startswith('model/'):return {'videos':cards,'last_page':1}
 if path=='search':return {'videos':cards,'last_page':1}
 return {}
if __name__=='__main__':
 import uvicorn
 uvicorn.run(app,host='127.0.0.1',port=8765,log_level='warning')
