'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validate}=require('../app/static/vision_proposals.js');
const scope={draftId:'draft-1',pictureIndex:0,baseAsset:'original',width:800,height:480};
const payload=()=>({format:'achewiw-vision-proposal',version:1,status:'unverified',autoApply:false,
 documentScope:{...scope},canvas:{width:800,height:480},
 proposals:[{id:'candidate-1',type:'text',confidence:.7,verification:'uncertain',
 x:10,y:20,w:100,h:20,text:'Узел А'}]});
test('внимание: предложения остаются непроверенными',()=>{
 const r=validate(payload(),scope);
 assert.equal(r.count,1);
 assert.equal(r.proposals[0].verification,'uncertain');
});
test('смешение черновиков запрещено',()=>{
 assert.throws(()=>validate(payload(),{...scope,draftId:'other'}));
 assert.throws(()=>validate(payload(),{...scope,pictureIndex:3}));
 assert.throws(()=>validate(payload(),{...scope,baseAsset:'new'}));
});
test('не допускается включение автоматического применения',()=>{
 const x=payload();x.autoApply=true;assert.throws(()=>validate(x,scope));
});
test('отклоняются повторы ID и неизвестные фигуры',()=>{
 const x=payload();x.proposals.push({...x.proposals[0]});
 assert.throws(()=>validate(x,scope));
 const y=payload();y.proposals[0].type='script';assert.throws(()=>validate(y,scope));
});
test('отклоняются некорректная уверенность и слишком длинный текст',()=>{
 const x=payload();x.proposals[0].confidence=NaN;assert.throws(()=>validate(x,scope));
 const y=payload();y.proposals[0].text='Ж'.repeat(181);assert.throws(()=>validate(y,scope));
});

test('отдельно принятая фигура становится ручной, не изменяя предложение',()=>{
 const {toManualShape}=require('../app/static/vision_proposals.js');
 const original=payload().proposals[0];
 const applied=toManualShape(original,scope);
 assert.equal(applied.type,'text');
 assert.equal(applied.text,'Узел А');
 assert.equal(applied.color,'#176d47');
 assert.equal(original.color,undefined);
});
test('нельзя принять фигуру вне холста',()=>{
 const {toManualShape}=require('../app/static/vision_proposals.js');
 assert.throws(()=>toManualShape({...payload().proposals[0],x:790,w:100},scope));
 assert.throws(()=>toManualShape({...payload().proposals[0],x:-1},scope));
});
test('допускаются вертикальные и горизонтальные стрелки',()=>{
 const {toManualShape}=require('../app/static/vision_proposals.js');
 const p=payload().proposals[0];
 assert.equal(toManualShape({...p,type:'arrow',x:20,y:20,w:0,h:100},scope).type,'arrow');
 assert.equal(toManualShape({...p,type:'line',x:20,y:20,w:100,h:0},scope).type,'line');
});
test('неприемлемые пустые надписи и точки не переводятся в ручные фигуры',()=>{
 const {toManualShape}=require('../app/static/vision_proposals.js');
 const p=payload().proposals[0];
 assert.throws(()=>toManualShape({...p,text:'  '},scope));
 assert.throws(()=>toManualShape({...p,type:'arrow',w:0,h:0},scope));
});
