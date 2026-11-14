import test from 'node:test';
import assert from 'node:assert/strict';
import {chooseSplits,validateLabels} from '../prepare-dataset.mjs';

test('YOLO labels accept only finite class IDs and boxes within image',()=>{
  assert.deepEqual(validateLabels('2 0.5 0.6 0.4 0.2\n'),[[2,.5,.6,.4,.2]]);
  assert.deepEqual(validateLabels(''),[]);
  for(const value of ['4 .5 .5 .2 .2','-1 .5 .5 .2 .2','.5 .5 .5 .2 .2','0 NaN .5 .2 .2','0 .5 .5 0 .2','0 .99 .5 .2 .2','0 .5 .5 .2','0 .5 .5 Infinity .2','0x1 .5 .5 .2 .2'])assert.throws(()=>validateLabels(value));
});
test('capture groups never overlap across train, val and test',()=>{
  const rows=Array.from({length:12},(_,i)=>({group:`scene-${Math.floor(i/2)}`,labels:[[0,.5,.5,.2,.2],[1,.5,.5,.2,.2],[2,.5,.5,.2,.2],[3,.5,.5,.2,.2]]}));
  const splits=chooseSplits(rows),seen=new Set();
  for(const data of Object.values(splits)){assert.ok(data.length);const groups=new Set(data.map(r=>r.group));for(const g of groups){assert.equal(seen.has(g),false);seen.add(g);}}
  assert.equal(Object.values(splits).flat().length,rows.length);
  assert.deepEqual(chooseSplits(rows),splits);
});
test('single capture group cannot be disguised as independent validation data',()=>{
  assert.throws(()=>chooseSplits(Array.from({length:100},()=>({group:'one-session',labels:[[0,.5,.5,.2,.2]]}))),/3 independent/);
});
test('missing classes cannot silently become a four-class dataset',()=>{
  assert.throws(()=>chooseSplits(Array.from({length:6},(_,i)=>({group:`g${i}`,labels:[[2,.5,.5,.2,.2]]}))),/all 4 classes/);
});
