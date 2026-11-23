/* Проверки R3.7B без GPU, браузера, SQLite и доступа к сети. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {inspect}=require('../app/static/diagram_graph.js');
const rect=(id,x,y,w=100,h=80)=>({id,type:'rectangle',x,y,w,h});
const ellipse=(id,x,y,w=100,h=80)=>({id,type:'ellipse',x,y,w,h});
const edge=(id,type,x,y,w,h)=>({id,type,x,y,w,h});
test('стрелка между двумя блоками — только кандидат, не подтверждённая связь',()=>{
 const report=inspect([rect('a',0,0),rect('b',200,0),edge('e','arrow',50,40,200,0)]);
 assert.equal(report.summary.candidateEdges,1);
 assert.deepEqual(report.edges[0].fromCandidates,['a']);
 assert.deepEqual(report.edges[0].toCandidates,['b']);
 assert.equal(report.edges[0].confirmed,false);
 assert.equal(report.confirmedRelations,0);
});
test('открытый конец линии не угадывается',()=>{
 const report=inspect([rect('a',0,0),edge('e','line',30,30,130,0)]);
 assert.equal(report.summary.unresolvedEdges,1);
 assert.deepEqual(report.edges[0].issues,['end-outside']);
});
test('перекрытие узлов помечается как неоднозначное',()=>{
 const report=inspect([rect('a',0,0),rect('b',0,0),rect('c',200,0),
 edge('e','arrow',30,30,210,0)]);
 assert.equal(report.edges[0].status,'unresolved');
 assert.ok(report.edges[0].issues.includes('start-ambiguous'));
});
test('петля в том же узле требует проверки',()=>{
 const report=inspect([rect('a',0,0),edge('e','arrow',10,10,50,50)]);
 assert.ok(report.edges[0].issues.includes('same-node'));
});
test('эллипс не принимает прямоугольный угол за внутреннюю точку',()=>{
 const report=inspect([ellipse('a',0,0),edge('e','line',0,0,200,0)]);
 assert.ok(report.edges[0].issues.includes('start-outside'));
});
test('надписи остаются непривязанными',()=>{
 const report=inspect([rect('a',0,0),{id:'t',type:'text',x:10,y:10,w:0,h:0,text:'Тест <&>'}]);
 assert.equal(report.summary.unassignedLabels,1);
 assert.equal(report.labels[0].assignment,'not-reviewed');
});
test('нельзя смешивать два объекта с одинаковым id',()=>{
 assert.throws(()=>inspect([rect('a',0,0),rect('a',100,0)]));
});
test('набор более 100 объектов отклоняется',()=>{
 assert.throws(()=>inspect(Array.from({length:101},(_,i)=>rect('x'+i,i,0))));
});

test('только человек может подтвердить однозначного кандидата',()=>{
 const audit=inspect([rect('a',0,0),rect('b',200,0),edge('e','arrow',50,40,200,0)]);
 const {applyReviews}=require('../app/static/diagram_graph.js');
 const reviewed=applyReviews(audit,{'e':'accepted'});
 assert.equal(reviewed.confirmedRelations,1);
 assert.equal(reviewed.edges[0].confirmed,true);
 assert.equal(audit.confirmedRelations,0);
});
test('неоднозначное соединение не подтверждается даже старым решением',()=>{
 const audit=inspect([rect('a',0,0),edge('e','arrow',50,40,200,0)]);
 const {applyReviews}=require('../app/static/diagram_graph.js');
 const reviewed=applyReviews(audit,{'e':'accepted'});
 assert.equal(reviewed.confirmedRelations,0);
 assert.equal(reviewed.edges[0].reviewDecision,'pending');
});
test('отклонённая связь остаётся неподтверждённой',()=>{
 const audit=inspect([rect('a',0,0),rect('b',200,0),edge('e','line',50,40,200,0)]);
 const {applyReviews}=require('../app/static/diagram_graph.js');
 const reviewed=applyReviews(audit,{'e':'rejected'});
 assert.equal(reviewed.reviewSummary.rejected,1);
 assert.equal(reviewed.edges[0].confirmed,false);
});

test('подпись закрепляется за узлом только по ручному назначению',()=>{
 const {applyLabelReviews}=require('../app/static/diagram_graph.js');
 const report=inspect([rect('n1',0,0),{id:'label',type:'text',x:15,y:10,w:0,h:0,text:'Контроллер'}]);
 const result=applyLabelReviews(report,{label:'n1'});
 assert.equal(result.confirmedLabels,1);
 assert.equal(result.labels[0].nodeId,'n1');
 assert.equal(report.labels[0].assignment,'not-reviewed');
});
test('несуществующий узел не подтверждает подпись',()=>{
 const {applyLabelReviews}=require('../app/static/diagram_graph.js');
 const report=inspect([rect('n1',0,0),{id:'label',type:'text',x:15,y:10,w:0,h:0,text:'Контроллер'}]);
 const result=applyLabelReviews(report,{label:'missing'});
 assert.equal(result.confirmedLabels,0);
 assert.equal(result.labels[0].nodeId,null);
});
test('очистка ручного назначения возвращает подпись без связи',()=>{
 const {applyLabelReviews}=require('../app/static/diagram_graph.js');
 const report=inspect([rect('n1',0,0),{id:'label',type:'text',x:15,y:10,w:0,h:0,text:'Контроллер'}]);
 assert.equal(applyLabelReviews(report,{}).labelReviewSummary.pending,1);
});
