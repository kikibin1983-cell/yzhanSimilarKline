import {BLOCKS,inBounds,chunkKey,editKey} from './blocks.js';
import {Generator} from './generation.js';
export class World {
  constructor(seed,edits=[]) {
    this.seed=seed; this.generator=new Generator(seed); this.chunks=new Map();
    this.edits=new Map(edits); this.dirty=new Set(); this.revision=0;
    this.chunkEdits=new Map();
    for(const [key,id] of this.edits) this.indexEdit(key,id);
  }
  indexEdit(key,id) {
    const [x,,z]=key.split(',').map(Number), ck=chunkKey(Math.floor(x/16),Math.floor(z/16));
    if(!this.chunkEdits.has(ck)) this.chunkEdits.set(ck,new Map());
    if(id===undefined) {
      this.chunkEdits.get(ck).delete(key);
      if(!this.chunkEdits.get(ck).size) this.chunkEdits.delete(ck);
    } else this.chunkEdits.get(ck).set(key,id);
  }
  loadChunk(cx,cz) {
    const key=chunkKey(cx,cz);
    if(!this.chunks.has(key)) {
      const data=this.generator.generateChunk(cx,cz);
      for(const [pos,id] of this.chunkEdits.get(key)??[]) {
        const [x,y,z]=pos.split(',').map(Number); data[(y*16+(z-cz*16))*16+(x-cx*16)]=id;
      }
      this.chunks.set(key,data);
    }
    return this.chunks.get(key);
  }
  getBlock(x,y,z) {
    if(!inBounds(x,y,z)) return y>=96 ? 0 : 13;
    const cx=Math.floor(x/16),cz=Math.floor(z/16);
    return this.loadChunk(cx,cz)[(y*16+z-cz*16)*16+x-cx*16];
  }
  setBlock(x,y,z,id) {
    if(!inBounds(x,y,z) || y===0 || !BLOCKS[id] || !Number.isInteger(id)) return false;
    const old=this.getBlock(x,y,z); if(old===id) return false;
    const cx=Math.floor(x/16),cz=Math.floor(z/16), key=editKey(x,y,z);
    this.loadChunk(cx,cz)[(y*16+z-cz*16)*16+x-cx*16]=id;
    if(id===this.generator.getBlock(x,y,z)) {this.edits.delete(key);this.indexEdit(key,undefined);}
    else {this.edits.set(key,id);this.indexEdit(key,id);}
    this.dirty.add(chunkKey(cx,cz));
    if(x-cx*16===0) this.dirty.add(chunkKey(cx-1,cz));
    if(x-cx*16===15) this.dirty.add(chunkKey(cx+1,cz));
    if(z-cz*16===0) this.dirty.add(chunkKey(cx,cz-1));
    if(z-cz*16===15) this.dirty.add(chunkKey(cx,cz+1));
    this.revision++; return true;
  }
  trimChunks(cx,cz,radius) {
    for(const key of this.chunks.keys()) {
      const [x,z]=key.split(',').map(Number);
      if(Math.abs(x-cx)>radius || Math.abs(z-cz)>radius) {this.chunks.delete(key);this.dirty.delete(key);}
    }
  }
}
