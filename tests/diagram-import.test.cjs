/* ACHEWIW R3.7E — JSON import contract (offline). */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validate}=require('../app/static/diagram_import.js');
const scope={draftId:'test-draft',pictureIndex:0,baseAsset:'original',width:800,height:480};
function sample(){
 const shapes=[
  {id:'shape-1',type:'rectangle',x:10,y:10,w:120,h:60,color:'#176d47',stroke:4},
  {id:'shape-2',type:'text',x:25,y:20,w:0,h:0,color:'#176d47',
   stroke:4,text:'Тест <&> схема',fontSize:28},
  {id:'shape-3',type:'arrow',x:80,y:30,w:140,h:0,color:'#176d47',stroke:4}
 ];
 return {format:'achewiw-verified-diagram-layer',version:1,
  documentScope:{...scope},origin:'manual',recognition:'not-performed',
  sourceRasterEmbedded:false,canvas:{width:800,height:480},shapes,
  geometryAudit:{edges:[{id:'shape-3',reviewDecision:'rejected'}],
    labels:[{id:'shape-2',assignment:'manual-confirmed',nodeId:'shape-1'}]}};
}
test('импорт строго привязан к черновику и рисунку',()=>{
 assert.equal(validate(sample(),scope).shapes.length,3);
 assert.throws(()=>validate(sample(),{...scope,pictureIndex:1}));
 assert.throws(()=>validate(sample(),{...scope,draftId:'other'}));
 assert.throws(()=>validate(sample(),{...scope,baseAsset:'replacement'}));
});
test('подпись и рецензия переносятся из JSON',()=>{
 const v=validate(sample(),scope);
 assert.equal(v.shapes[1].text,'Тест <&> схема');
 assert.equal(v.decisions['shape-3'],'rejected');
 assert.equal(v.assignments['shape-2'],'shape-1');
});
test('не доверять посторонним подписям и связям',()=>{
 const p=sample();
 p.geometryAudit.labels[0].nodeId='unknown';
 assert.throws(()=>validate(p,scope));
 const q=sample();q.geometryAudit.edges[0].id='not-real';
 assert.throws(()=>validate(q,scope));
});
test('отклонять опасные и повреждённые данные',()=>{
 const p=sample();p.shapes[1].text='<script>'.repeat(100);
 assert.throws(()=>validate(p,scope));
 const q=sample();q.shapes[0].x=Infinity;
 assert.throws(()=>validate(q,scope));
 const r=sample();r.canvas.width=799;
 assert.throws(()=>validate(r,scope));
});
test('порядок id обязателен, чужие версии запрещены',()=>{
 const p=sample();p.shapes[0].id='shape-99';
 assert.throws(()=>validate(p,scope));
 const q=sample();q.version=2;
 assert.throws(()=>validate(q,scope));
});
