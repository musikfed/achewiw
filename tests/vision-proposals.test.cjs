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
