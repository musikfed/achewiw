/* ACHEWIW R3.7E — strict import validator, no document writes. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.ACHEWIWDiagramImport=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const types=new Set(['rectangle','ellipse','cover','line','arrow','text']);
  const finite=(n)=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=16000000;
  function validate(payload,scope){
    if(!payload||payload.format!=='achewiw-verified-diagram-layer'||payload.version!==1||
       payload.origin!=='manual'||payload.recognition!=='not-performed'||
       payload.sourceRasterEmbedded!==false)throw Error('Неподдерживаемый формат или источник схемы');
    if(!scope||typeof scope.draftId!=='string'||!scope.draftId||
       !Number.isInteger(scope.pictureIndex)||scope.pictureIndex<0||
       typeof scope.baseAsset!=='string')throw Error('Нет защищённого контекста DOCX');
    if(!payload.documentScope||payload.documentScope.draftId!==scope.draftId||
       payload.documentScope.pictureIndex!==scope.pictureIndex||
       payload.documentScope.baseAsset!==scope.baseAsset)
      throw Error('JSON относится к другому черновику, рисунку или версии');
    if(!payload.canvas||payload.canvas.width!==scope.width||
       payload.canvas.height!==scope.height)
      throw Error('Размеры холста не совпадают с исходным рисунком');
    if(!Array.isArray(payload.shapes)||payload.shapes.length>100)
      throw Error('Некорректное количество фигур');
    const shapes=payload.shapes.map((s,i)=>{
      if(!s||s.id!=='shape-'+(i+1)||!types.has(s.type)||
         ![s.x,s.y,s.w,s.h].every(finite)||
         typeof s.color!=='string'||!/^#[0-9a-f]{6}$/i.test(s.color)||
         !finite(s.stroke)||s.stroke<1||s.stroke>30)
        throw Error('Недопустимая фигура №'+(i+1));
      if(s.type==='text'&&(typeof s.text!=='string'||s.text.length>180||
         !finite(s.fontSize)||s.fontSize<10||s.fontSize>100))
        throw Error('Недопустимая надпись №'+(i+1));
      return {type:s.type,x:s.x,y:s.y,w:s.w,h:s.h,color:s.color,
        stroke:s.stroke,...(s.type==='text'?{text:s.text,fontSize:s.fontSize}:{})};
    });
    const edges=new Set(payload.shapes.filter(s=>s.type==='line'||s.type==='arrow').map(s=>s.id));
    const nodes=new Set(payload.shapes.filter(s=>s.type==='rectangle'||s.type==='ellipse').map(s=>s.id));
    const labels=new Set(payload.shapes.filter(s=>s.type==='text').map(s=>s.id));
    const decisions=Object.create(null),assignments=Object.create(null);
    if(payload.geometryAudit!=null){
      const audit=payload.geometryAudit;
      if(!audit||!Array.isArray(audit.edges)||!Array.isArray(audit.labels)||
         audit.edges.length>100||audit.labels.length>100)
        throw Error('Некорректная структура проверки схемы');
      const seenEdges=new Set(),seenLabels=new Set();
      for(const edge of audit.edges){
        if(!edge||!edges.has(edge.id)||seenEdges.has(edge.id))throw Error('Неизвестная связь');
        seenEdges.add(edge.id);
        if(edge.reviewDecision==='accepted'||edge.reviewDecision==='rejected')
          decisions[edge.id]=edge.reviewDecision;
        else if(edge.reviewDecision!=null&&edge.reviewDecision!=='pending')
          throw Error('Недопустимое решение по связи');
      }
      for(const label of audit.labels){
        if(!label||!labels.has(label.id)||seenLabels.has(label.id))
          throw Error('Неизвестная надпись');
        seenLabels.add(label.id);
        if(label.assignment==='manual-confirmed'){
          if(!nodes.has(label.nodeId))throw Error('Надпись назначена неизвестному узлу');
          assignments[label.id]=label.nodeId;
        }else if(label.assignment!=='not-reviewed')throw Error('Недопустимое назначение');
      }
      if(seenEdges.size!==edges.size||seenLabels.size!==labels.size)
        throw Error('Не все связи и подписи отражены в отчёте');
    }
    return {shapes,decisions,assignments};
  }
  return {validate};
});
