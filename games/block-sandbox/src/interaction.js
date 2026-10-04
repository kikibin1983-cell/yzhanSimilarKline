import {isSolid,inBounds,BUILDABLE} from './blocks.js';
export function raycast(world,origin,direction,maxDistance=6) {
  const axes=['x','y','z'],length=Math.hypot(direction.x,direction.y,direction.z);
  if(!length || !Number.isFinite(length))return null;
  const cell={x:Math.floor(origin.x),y:Math.floor(origin.y),z:Math.floor(origin.z)},step={},delta={},next={};
  for(const a of axes){const d=direction[a]/length;step[a]=Math.sign(d);delta[a]=d===0?Infinity:Math.abs(1/d);next[a]=d===0?Infinity:((d>0?cell[a]+1:cell[a])-origin[a])/d;}
  let distance=0,adjacent={...cell};
  for(let i=0;i<64&&distance<=maxDistance;i++) {
    if(inBounds(cell.x,cell.y,cell.z)&&isSolid(world.getBlock(cell.x,cell.y,cell.z)))return {block:{...cell},adjacent,distance};
    const a=next.x<=next.y&&next.x<=next.z?'x':next.y<=next.z?'y':'z';
    adjacent={...cell};distance=next[a];cell[a]+=step[a];next[a]+=delta[a];
  }
  return null;
}
export function editBlock(world,player,hit,id,placing) {
  if(!hit || (hit.distance??0)>6)return false;
  const p=placing?hit.adjacent:hit.block;
  if(!inBounds(p.x,p.y,p.z)||p.y===0)return false;
  if(placing&&(!BUILDABLE.includes(id)||world.getBlock(p.x,p.y,p.z)!==0||player.intersectsBlock(p.x,p.y,p.z)))return false;
  return world.setBlock(p.x,p.y,p.z,placing?id:0);
}
