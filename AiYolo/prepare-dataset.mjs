import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
export const classes=JSON.parse(fs.readFileSync(path.join(root,'classes.json'),'utf8'));
export function validateLabels(text,filename='labels') {
  const rows=[];
  for(const [index,line] of text.split(/\r?\n/).entries()) {
    if(!line.trim()) continue;
    const fields=line.trim().split(/\s+/);
    if(fields.some(v=>! /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(v)))throw new Error(`${filename}:${index+1}: expected decimal numeric values`);
    const values=fields.map(Number);
    if(values.length!==5 || values.some(v=>!Number.isFinite(v))) throw new Error(`${filename}:${index+1}: expected class xc yc width height`);
    const [id,x,y,w,h]=values;
    if(!Number.isInteger(id)||id<0||id>=classes.length||w<=0||h<=0||x-w/2 < -1e-6||y-h/2 < -1e-6||x+w/2>1.000001||y+h/2>1.000001) throw new Error(`${filename}:${index+1}: invalid class or box outside image`);
    rows.push(values);
  }
  return rows;
}
function random(seed) { let n=seed>>>0;return()=>{n=(Math.imul(n,1664525)+1013904223)>>>0;return n/4294967296;}; }
export function chooseSplits(items) {
  const grouped=new Map();
  for(const item of items) { if(!grouped.has(item.group))grouped.set(item.group,[]);grouped.get(item.group).push(item); }
  if(grouped.size<3)throw new Error('At least 3 independent capture groups are required for train/val/test. Do not split adjacent frames from one series.');
  const groupNames=[...grouped.keys()].sort();
  let best=null;
  for(let attempt=0;attempt<2000;attempt++) {
    const rand=random(42+attempt),names=[...groupNames];
    for(let i=names.length-1;i>0;i--){const j=Math.floor(rand()*(i+1));[names[i],names[j]]=[names[j],names[i]];}
    const sizes={test:Math.max(1,Math.round(names.length*.15)),val:Math.max(1,Math.round(names.length*.15))};
    const splits={test:[],val:[],train:[]};
    names.forEach((group,i)=>splits[i<sizes.test?'test':i<sizes.test+sizes.val?'val':'train'].push(...grouped.get(group)));
    const missing=Object.values(splits).some(rows=>classes.some(c=>!rows.some(row=>row.labels.some(box=>box[0]===c.id))));
    if(missing)continue;
    const score=Math.abs(splits.train.length/items.length-.7)+Math.abs(splits.val.length/items.length-.15)+Math.abs(splits.test.length/items.length-.15);
    if(!best||score<best.score)best={score,splits};
  }
  if(!best)throw new Error('Cannot place all 4 classes into independent train/val/test groups. Collect more groups with each defect; do not duplicate images or relabel normal parts.');
  return best.splits;
}
export function prepare(input,output) {
  input=path.resolve(input);output=path.resolve(output);
  if(fs.existsSync(output))throw new Error(`Output already exists: ${output}. Choose a new output directory to preserve the previous dataset.`);
  const annotationRoot=path.join(input,'labels'),imageRoot=path.join(input,'images');
  const manifest=JSON.parse(fs.readFileSync(path.join(input,'manifest.json'),'utf8'));
  if(manifest.version!==1||JSON.stringify(manifest.classes)!==JSON.stringify(classes.map(c=>c.code)))throw new Error('Unknown annotation format or class ordering.');
  if(!Array.isArray(manifest.images)||!manifest.images.length)throw new Error('No annotated images in manifest.');
  const hashes=new Set(),names=new Set();
  const items=manifest.images.map(entry=>{
    if(typeof entry.file!=='string'||entry.file!==path.basename(entry.file)||!/^image_[0-9]{4,}\.jpg$/.test(entry.file))throw new Error('Invalid image filename.');
    if(names.has(entry.file))throw new Error(`Duplicate manifest entry: ${entry.file}`);names.add(entry.file);
    if(typeof entry.group!=='string'||!entry.group.trim()||entry.reviewed!==true)throw new Error(`${entry.file}: a capture group and confirmed annotation are required.`);
    const source=path.join(imageRoot,entry.file),label=path.join(annotationRoot,entry.file.replace(/\.jpg$/,'.txt'));
    const data=fs.readFileSync(source);
    if(data.length<4||data[0]!==0xff||data[1]!==0xd8)throw new Error(`${entry.file}: expected normalized JPEG from the annotation tool.`);
    const hash=crypto.createHash('sha256').update(data).digest('hex');
    if(hashes.has(hash))throw new Error(`Exact duplicate image: ${entry.file}. Remove duplicates before splitting.`);hashes.add(hash);
    const labels=validateLabels(fs.readFileSync(label,'utf8'),entry.file);
    if(!labels.length&&entry.negative!==true)throw new Error(`${entry.file}: empty labels require explicit negative confirmation.`);
    return {...entry,group:entry.group.trim(),labels,source,label,sha256:hash};
  });
  const splits=chooseSplits(items);
  fs.mkdirSync(output,{recursive:true});
  const summary={classes:classes.map(c=>c.code),splits:{}};
  for(const [split,rows] of Object.entries(splits)) {
    fs.mkdirSync(path.join(output,'images',split),{recursive:true});
    fs.mkdirSync(path.join(output,'labels',split),{recursive:true});
    for(const row of rows){fs.copyFileSync(row.source,path.join(output,'images',split,row.file));fs.copyFileSync(row.label,path.join(output,'labels',split,row.file.replace('.jpg','.txt')));}
    summary.splits[split]={images:rows.length,groups:[...new Set(rows.map(r=>r.group))],negatives:rows.filter(r=>!r.labels.length).length,boxes:Object.fromEntries(classes.map(c=>[c.code,rows.reduce((sum,r)=>sum+r.labels.filter(b=>b[0]===c.id).length,0)]))};
  }
  const yaml=`path: ${JSON.stringify(output.replaceAll('\\','/'))}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n${classes.map(c=>`  ${c.id}: ${c.code}`).join('\n')}\n`;
  fs.writeFileSync(path.join(output,'dataset.yaml'),yaml);
  fs.writeFileSync(path.join(output,'summary.json'),JSON.stringify(summary,null,2));
  fs.writeFileSync(path.join(output,'provenance.json'),JSON.stringify(Object.fromEntries(Object.entries(splits).map(([split,rows])=>[split,rows.map(({file,group,sourceUrl,sha256})=>({file,group,sourceUrl,sha256}))])),null,2));
  return summary;
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  try {
    const [input,output]=process.argv.slice(2);
    if(!input||!output)throw new Error('Usage: node prepare-dataset.mjs <unpacked annotations folder> <new dataset folder>');
    console.log(JSON.stringify(prepare(input,output),null,2));
  }catch(error){console.error(error.message);process.exitCode=1;}
}
