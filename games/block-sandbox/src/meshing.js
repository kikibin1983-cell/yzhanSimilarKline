import {BLOCKS} from './blocks.js';
// 四角按从外侧看逆时针排序；每个面拆为两个三角形。
const FACES=[
  {n:[1,0,0],v:[[1,0,1],[1,0,0],[1,1,0],[1,1,1]]},
  {n:[-1,0,0],v:[[0,0,0],[0,0,1],[0,1,1],[0,1,0]]},
  {n:[0,1,0],v:[[0,1,1],[1,1,1],[1,1,0],[0,1,0]]},
  {n:[0,-1,0],v:[[0,0,0],[1,0,0],[1,0,1],[0,0,1]]},
  {n:[0,0,1],v:[[0,0,1],[1,0,1],[1,1,1],[0,1,1]]},
  {n:[0,0,-1],v:[[1,0,0],[0,0,0],[0,1,0],[1,1,0]]},
];
export function meshChunk(world,cx,cz) {
  const groups={opaque:{positions:[],normals:[],uvs:[]},transparent:{positions:[],normals:[],uvs:[]},water:{positions:[],normals:[],uvs:[]}};
  for(let y=0;y<96;y++) for(let z=cz*16;z<cz*16+16;z++) for(let x=cx*16;x<cx*16+16;x++) {
    const id=world.getBlock(x,y,z); if(!id) continue;
    const block=BLOCKS[id],group=groups[id===12?'water':block.transparent?'transparent':'opaque'];
    for(const face of FACES) {
      const other=world.getBlock(x+face.n[0],y+face.n[1],z+face.n[2]);
      if(other===id || (other && !BLOCKS[other].transparent)) continue;
      const tile=face.n[1]===1?(block.top??block.tile):face.n[1]===-1?(block.bottom??block.tile):block.tile;
      const tx=tile%4,ty=Math.floor(tile/4),uv=[[0,0],[1,0],[1,1],[0,1]];
      for(const i of [0,1,2,0,2,3]) {
        const v=face.v[i]; group.positions.push(x+v[0],y+v[1],z+v[2]);group.normals.push(...face.n);
        // atlas 的每格内部留半像素，最近邻采样避免跨格。
        group.uvs.push((tx+(uv[i][0]*15+.5)/16)/4,1-(ty+((1-uv[i][1])*15+.5)/16)/4);
      }
    }
  }
  for(const group of Object.values(groups)) for(const prop of ['positions','normals','uvs']) group[prop]=new Float32Array(group[prop]);
  return groups;
}
