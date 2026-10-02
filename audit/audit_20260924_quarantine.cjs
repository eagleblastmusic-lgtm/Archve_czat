// Read-only audit counterexamples: loads production JS into an isolated VM.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('static/video-prefetch.js', 'utf8');
function setup(videos) {
  const state = {videos, gridCardMap: new Map()};
  const cards = videos.map(v => ({dataset:{source:v.source,videoId:v.id},_videoData:v,
    remove(){this.removed=true;},_updateCard(){}}));
  const store = new Map();
  const ctx = {console,setTimeout,clearTimeout,AbortController,
    ArchivebateAppContext:{state,dom:{}},
    document:{querySelectorAll:()=>cards.filter(c=>!c.removed)},
    localStorage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)}};
  ctx.window=ctx; vm.createContext(ctx); vm.runInContext(source,ctx);
  vm.runInContext(fs.readFileSync('static/video-grid.js','utf8'),ctx);
  return {state,cards,api:ctx.ArchivebateVideoPrefetch,grid:ctx.ArchivebateVideoGrid};
}
const member = id => ({id,source:'archivebate'});
const group = (id,n,lazy=false) => ({...member(id),username:id,is_grouped:true,
  group_count:n,group_members_lazy:lazy,
  ...(lazy?{}:{grouped_videos:[member(id),member(id+'-live')]})});
const results=[];
{
 const other=group('other',100,true), target=member('dead');
 const t=setup([target,other]);t.api.markUnavailableVideo('dead');
 assert.equal(other.group_count,99);
 results.push({case:'unrelated_lazy_group',expected_count:100,actual_count:other.group_count});
}
{
 const v=group('dead',2,true), t=setup([v]);t.api.markUnavailableVideo('dead');
 const survivedBefore=t.state.videos.length;
 assert.equal(t.state.videos.length,1); // lazy marker still protects aggregate
 const rendered=t.grid.filterKnownUnavailableVideos(t.state.videos);
 assert.equal(rendered.length,0);
 results.push({case:'lazy_group_two_members_hidden_on_render',id:v.id,count:v.group_count,
   representative_unavailable:v._representativeUnavailable,retained_in_state:survivedBefore===1,
   expected_visible_groups:1,actual_visible_groups:rendered.length});
}
{
 const v=group('good',2), t=setup([v]);t.api.markUnavailableVideo('unrelated');
 assert.equal(v.group_count,1);assert.equal(v.grouped_videos.length,2);
 results.push({case:'unrelated_loaded_group',expected_count:2,actual_count:v.group_count,
  retained_members:v.grouped_videos.length,group_flag:v.is_grouped});
}
{
 const v={id:'same',source:'camwhores'}, t=setup([member('same'),v]);
 t.api.markUnavailableVideo('same'); assert.equal(t.state.videos.length,0);
 results.push({case:'cross_provider_raw_id',expected_remaining:1,actual_remaining:t.state.videos.length,
  camwhores_DOM_removed:Boolean(t.cards[1].removed)});
}
{
 const v=group('dead',3), t=setup([v]);
 t.api.markUnavailableVideo('dead');assert.equal(v.id,'dead-live');
 results.push({case:'loaded_group_leader_promotion',result:'works',remaining:v.group_count});
}
console.log(JSON.stringify(results,null,2));
