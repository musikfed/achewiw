/* ACHEWIW R3.7F — untrusted vision proposal review. No image/model calls. */
(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.ACHEWIWVisionProposals=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const kinds=new Set(['rectangle','ellipse','line','arrow','text']);
  function validate(raw,scope){
    if(!scope||!Number.isInteger(scope.width)||!Number.isInteger(scope.height)||
        !scope.draftId||!Number.isInteger(scope.pictureIndex)||
        typeof scope.baseAsset!=='string')throw Error('Нет контекста рисунка');
    if(!raw||raw.format!=='achewiw-vision-proposal'||raw.version!==1||
        raw.status!=='unverified'||raw.autoApply!==false||
        !raw.documentScope||raw.documentScope.draftId!==scope.draftId||
        raw.documentScope.pictureIndex!==scope.pictureIndex||
        raw.documentScope.baseAsset!==scope.baseAsset||
        raw.canvas?.width!==scope.width||raw.canvas?.height!==scope.height)
      throw Error('Предложение не относится к текущему рисунку или формат неизвестен');
    if(!Array.isArray(raw.proposals)||raw.proposals.length>100)
      throw Error('Некорректное количество предположений');
    const ids=new Set();
    const clean=raw.proposals.map(p=>{
      if(!p||typeof p.id!=='string'||!/^candidate-[1-9][0-9]*$/.test(p.id)||
         ids.has(p.id)||!kinds.has(p.type)||typeof p.confidence!=='number'||
         !Number.isFinite(p.confidence)||p.confidence<0||p.confidence>1||
         !['unverified','uncertain'].includes(p.verification)||
         ![p.x,p.y,p.w,p.h].every(v=>typeof v==='number'&&Number.isFinite(v)&&
            v>=-16000000&&v<=16000000))
        throw Error('Неверное предположение');
      if(p.type==='text'&&(typeof p.text!=='string'||p.text.length>180))
        throw Error('Неверная предложенная надпись');
      ids.add(p.id);
      return {id:p.id,type:p.type,confidence:p.confidence,verification:p.verification,
        x:p.x,y:p.y,w:p.w,h:p.h,...(p.type==='text'?{text:p.text}:{})};
    });
    return {proposals:clean,count:clean.length};
  }
  return {validate};
});
