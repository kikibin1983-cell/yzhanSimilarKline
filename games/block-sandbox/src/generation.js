import {inBounds} from './blocks.js';
export const GENERATOR_VERSION = 1;
export const SEA_LEVEL = 24;
function seedHash(seed) {
  let h=2166136261;
  for (const ch of seed) {h^=ch.charCodeAt(0); h=Math.imul(h,16777619);}
  return h>>>0;
}
// 坐标哈希没有全局随机状态，加载顺序不会改变世界。
export class Generator {
  constructor(seed) {this.seed=seed; this.hash=seedHash(seed);}
  random(x,z,salt=0) {
    let h=this.hash ^ Math.imul(x,374761393) ^ Math.imul(z,668265263) ^ Math.imul(salt,1274126177);
    h=Math.imul(h^(h>>>13),1274126177); return ((h^(h>>>16))>>>0)/4294967296;
  }
  noise(x,z,scale,salt) {
    const fx=x/scale, fz=z/scale, ix=Math.floor(fx), iz=Math.floor(fz);
    const ease=t=>t*t*(3-2*t), a=ease(fx-ix), b=ease(fz-iz);
    const p=this.random(ix,iz,salt)*(1-a)+this.random(ix+1,iz,salt)*a;
    const q=this.random(ix,iz+1,salt)*(1-a)+this.random(ix+1,iz+1,salt)*a;
    return p*(1-b)+q*b;
  }
  surfaceHeight(x,z) {return Math.floor(18+this.noise(x,z,65,1)*23+this.noise(x,z,19,2)*7);}
  baseBlock(x,y,z,h=this.surfaceHeight(x,z)) {
    if(y===0) return 13;
    if(y>h) return y<=SEA_LEVEL ? 12 : 0;
    if(y===h) return h<=SEA_LEVEL+1 ? 4 : 1;
    if(y>=h-3) return h<=SEA_LEVEL+1 ? 4 : 2;
    // 连续的弯曲洞道，比逐格独立随机更适合探索。
    if(y>4 && y<h-5) {
      const tunnel=11+Math.sin(x*.09+this.hash%31)*4+Math.cos(z*.08)*3;
      const n=this.noise(x,z,24,7);
      if(Math.abs(y-tunnel)<2.4 && n>.34 && n<.70) return 0;
    }
    const ore=this.random(x+y*13,z-y*7,8);
    if(y<24 && ore>.972) return 11;
    if(y<33 && ore<.028) return 10;
    return 3;
  }
  treesFor(cx,cz) {
    const trees=[];
    for(let gx=Math.floor((cx*16-3)/7); gx<=Math.floor((cx*16+18)/7);gx++)
      for(let gz=Math.floor((cz*16-3)/7);gz<=Math.floor((cz*16+18)/7);gz++) {
        if(this.random(gx,gz,30)>.26) continue;
        const x=gx*7+2+Math.floor(this.random(gx,gz,31)*3), z=gz*7+2+Math.floor(this.random(gx,gz,32)*3);
        const h=this.surfaceHeight(x,z);
        if(h>SEA_LEVEL+2 && inBounds(x,h,z)) trees.push({x,z,h});
      }
    return trees;
  }
  treeBlock(t,x,y,z) {
    const dx=Math.abs(t.x-x), dz=Math.abs(t.z-z), dy=y-t.h;
    if(dx===0 && dz===0 && dy>=1 && dy<=5) return 5;
    if(dy>=4 && dy<=6 && dx<=2 && dz<=2 && !(dx===2 && dz===2) && (dy<6 || dx+dz<=1)) return 6;
    return 0;
  }
  getBlock(x,y,z) {
    if(!inBounds(x,y,z)) return y>=96 ? 0 : 13;
    const base=this.baseBlock(x,y,z);
    if(base!==0) return base;
    let leaf=0;
    for(const t of this.treesFor(Math.floor(x/16),Math.floor(z/16))) {
      const id=this.treeBlock(t,x,y,z); if(id===5) return id; if(id===6) leaf=6;
    }
    return leaf;
  }
  generateChunk(cx,cz) {
    const data=new Uint8Array(16*16*96), trees=this.treesFor(cx,cz);
    for(let z=0;z<16;z++) for(let x=0;x<16;x++) {
      const wx=cx*16+x,wz=cz*16+z,h=this.surfaceHeight(wx,wz);
      for(let y=0;y<=Math.max(h,SEA_LEVEL);y++) data[(y*16+z)*16+x]=this.baseBlock(wx,y,wz,h);
    }
    for(const t of trees) for(let y=t.h+1;y<=t.h+6;y++) for(let z=Math.max(0,t.z-cz*16-2);z<=Math.min(15,t.z-cz*16+2);z++)
      for(let x=Math.max(0,t.x-cx*16-2);x<=Math.min(15,t.x-cx*16+2);x++) {
        const i=(y*16+z)*16+x, id=this.treeBlock(t,cx*16+x,y,cz*16+z);
        if(id && (data[i]===0 || (id===5 && data[i]===6))) data[i]=id;
      }
    return data;
  }
}
