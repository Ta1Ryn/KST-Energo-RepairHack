import assert from 'node:assert/strict';

const base='http://127.0.0.1:8001';
const metadata=await (await fetch(base+'/models')).json();
const token={'X-Review-Token':metadata.token};
const prior=metadata.selection;
const jsonHeaders={...token,'Content-Type':'application/json'};
const photo=metadata.images.find(item=>item.split==='test');
const blob=await (await fetch(base+photo.imageUrl)).blob();
const before=await (await fetch(base+'/health')).json();
const form=()=>{const data=new FormData();data.set('file',blob,'case.jpg');data.set('models',metadata.models.map(m=>m.id).join(','));data.set('threshold','.25');return data};
const checks=[];
async function expect(route,options,status){const response=await fetch(base+route,options);assert.equal(response.status,status,await response.clone().text());checks.push(route+': '+status);return response}
const select=models=>fetch(base+'/selection',{method:'POST',headers:jsonHeaders,body:JSON.stringify({models,threshold:.25})});
function detectionsValid(items){for(const d of items){assert.ok(d.label!=='car'&&d.label!=='oil');assert.ok(d.confidence>=.25&&d.confidence<=1);const b=d.box;assert.ok(Object.values(b).every(Number.isFinite));assert.ok(b.x>=0&&b.y>=0&&b.width>0&&b.height>0&&b.x+b.width<=1.000001&&b.y+b.height<=1.000001);for(const point of d.polygon||[])assert.ok(point.every(v=>Number.isFinite(v)&&v>=0&&v<=1))}}
await expect('/selection',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(prior)},403);
await expect('/selection',{method:'POST',headers:{...jsonHeaders,Origin:'https://example.com'},body:JSON.stringify(prior)},403);
for(const models of [[],['unknown'],['corrosion','corrosion'],['legacy','corrosion']])await expect('/selection',{method:'POST',headers:jsonHeaders,body:JSON.stringify({models})},422);
await expect('/selection',{method:'POST',headers:jsonHeaders,body:JSON.stringify({models:['corrosion'],threshold:0})},422);
assert.deepEqual(await (await fetch(base+'/health')).json(),before);
await expect('/compare',{method:'POST',body:form()},403);
const bad=form();bad.set('file',new Blob(['bad image']),'bad.jpg');await expect('/compare',{method:'POST',headers:token,body:bad},415);
const result=await (await expect('/compare',{method:'POST',headers:token,body:form()},200)).json();
assert.equal(result.results.length,metadata.models.length);
for(const model of result.results){assert.equal(model.status,'ok',model.name);assert.ok(model.elapsedMs>0);detectionsValid(model.detections)}
assert.deepEqual((await (await fetch(base+'/models')).json()).selection,prior);
try{
 const activated=await select(metadata.models.map(m=>m.id));assert.equal(activated.status,200,await activated.clone().text());
 const data=form();for(const key of ['equipmentId','componentId','installationId'])data.set(key,'10000000-0000-0000-0000-000000000001');
 const analysis=await (await expect('/analyze',{method:'POST',body:data},200)).json();
 assert.match(analysis.modelVersion,/^repairhack-cv-v2-/);assert.equal(analysis.modelResults.length,metadata.models.length);detectionsValid(analysis.detections);
 assert.ok(analysis.detections.every(d=>!d.polygon||(d.polygon.length>=3&&d.polygon.length<=96)),'Segmentation contours must remain bounded');
 assert.ok(JSON.stringify(analysis).length<1024*1024);
}finally{
 const response=await fetch(base+'/selection',{method:'POST',headers:jsonHeaders,body:JSON.stringify(prior)});assert.equal(response.status,200,'Restore selected models');
}
assert.equal((await (await fetch(base+'/health')).json()).modelVersion,before.modelVersion);
console.log('PASS: '+metadata.models.length+'-model comparison, all-model analysis, normalized boxes/masks, rejected invalid input, local selection protection, config restoration. '+checks.length+' HTTP assertions.');
