const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const images=[];
 class ImageMock{set src(v){this.url=v;images.push(this)}}
 const source=fs.readFileSync('static/youtube-storyboard.js','utf8');
 const ctx={Image:ImageMock,DOMException,AbortController,setTimeout,clearTimeout,preloadMemory:new Map()};vm.createContext(ctx);
 vm.runInContext(source.slice(source.indexOf('  function consume('),source.indexOf('  async function fetchStatus')),ctx);
 const a=new AbortController(),b=new AbortController();
 const first=ctx.preload('/sprite',a.signal),second=ctx.preload('/sprite',b.signal);
 a.abort();await assert.rejects(first,{name:'AbortError'});assert.equal(images.length,1);
 images[0].onload();assert.equal((await second).url,'/sprite');assert.equal((await ctx.preload('/sprite')).url,'/sprite');
 const failing=ctx.preload('/broken');images[1].onerror();await assert.rejects(failing);const retry=ctx.preload('/broken');assert.equal(images.length,3);images[2].onload();await retry;
 console.log('PASS: cancelling one sprite consumer preserves others; retry after image error succeeds');
})().catch(e=>{console.error(e);process.exitCode=1});
