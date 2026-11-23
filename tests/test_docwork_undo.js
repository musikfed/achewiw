/* Node-only behavioral tests for the actual R3.3 undo implementation.
   No browser, packages, network, SQLite or production data required. */
'use strict';
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');

const source=fs.readFileSync('app/static/docwork.js','utf8');
const start=source.indexOf('/* R3.3: bounded multi-step action timeline.');
const end=source.indexOf('function rememberDraft() {',start);
assert.ok(start>=0&&end>start,'Undo logic exists in actual production JS');
const undoCode=source.slice(start,end);
const scaffolding=`
'use strict';
const controls=Object.create(null);
const $=id=>controls[id]||(controls[id]={
  disabled:false,textContent:'',value:'keep',dataset:{},
  setAttribute(){},querySelector(){return null;}
});
const overrides=Object.create(null);
const replacements=Object.create(null);
const paragraphAlignments=Object.create(null);
const imageEdits=Object.create(null);
const imageAssets=Object.create(null);
const imageMoves=Object.create(null);
let activeParagraph=null,imageMoveCounter=0;
const undoStack=[],redoStack=[];
const MAX_UNDO_STEPS=200,MAX_UNDO_CHARS=5_000_000;
let lastUndoKey='',lastUndoTime=0;
let report={type:'docx',paragraphs:[]};
function changeCount(){}
function markDraftDirty(){}
function drawRows(){}
function renderDocumentCanvas(){}
function renderImageGallery(){}
const window={ACHEWIWWorkbench:null};
`;
const checks=`
  editWithUndo('type:1',()=>{replacements['1']='Пр';},true);
  editWithUndo('type:1',()=>{replacements['1']='При';},true);
  editWithUndo('type:1',()=>{replacements['1']='Привет';},true);
  assert.equal(undoStack.length,1,'Continuous typing is a single undo step');
  assert.equal(replacements['1'],'Привет');
  undoEditAction();
  assert.equal(replacements['1'],undefined,'Undo restores before typing');
  assert.equal(redoStack.length,1);
  undoEditAction(true);
  assert.equal(replacements['1'],'Привет','Redo replays typing');

  editWithUndo('align:1',()=>{paragraphAlignments['1']='justify';});
  editWithUndo('figure:0',()=>{imageMoves['0']={before_paragraph:5,order:1};});
  editWithUndo('figure-style:0',()=>{
    imageEdits['0']={width_percent:65,align:'center',corner_percent:16};
  });
  assert.equal(undoStack.length,4);
  undoEditAction();
  assert.equal(imageEdits['0'],undefined,'Undo figure styling');
  undoEditAction();
  assert.equal(imageMoves['0'],undefined,'Undo figure move');
  undoEditAction();
  assert.equal(paragraphAlignments['1'],undefined,'Undo paragraph alignment');
  undoEditAction(true);
  assert.equal(paragraphAlignments['1'],'justify','Redo alignment');
  editWithUndo('new:1',()=>{overrides['1']='heading1';});
  assert.equal(redoStack.length,0,'New change clears redo history');

  resetUndoTimeline();
  assert.equal(undoStack.length,0,'New document clears prior undo history');
  for(let i=0;i<250;i++){
    editWithUndo('item:'+i,()=>{paragraphAlignments[String(i)]='center';});
  }
  assert.equal(undoStack.length,200,'200-step history is bounded');
  assert.equal(redoStack.length,0);
  assert.equal($('undoEdit').disabled,false);
  assert.equal($('redoEdit').disabled,true);
  console.log('DOCWORK UNDO JS PASS: grouped typing, undo/redo, text/figure/align, branch reset and 200-step cap');
`;
vm.runInNewContext(scaffolding+undoCode+checks,{
  assert,console,Date,JSON,Object,Array,Math
},{filename:'docwork-undo-behavior.js',timeout:15000});
