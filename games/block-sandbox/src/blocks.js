// 方块数据是添加新方块的入口；ID 属于存档协议，不要重新编号。
export const BLOCKS = [
  {name:'空气', solid:false},
  {name:'草方块', solid:true, tile:1, top:0, bottom:2, color:'#6f9d43'},
  {name:'泥土', solid:true, tile:2, color:'#936644'},
  {name:'石头', solid:true, tile:3, color:'#89918e'},
  {name:'沙子', solid:true, tile:4, color:'#d8c58b'},
  {name:'原木', solid:true, tile:5, top:6, bottom:6, color:'#80613a'},
  {name:'树叶', solid:true, tile:7, color:'#487c3c'},
  {name:'木板', solid:true, tile:8, color:'#bc925c'},
  {name:'砖块', solid:true, tile:9, color:'#ac6755'},
  {name:'玻璃', solid:true, transparent:true, tile:10, color:'#adcdd1'},
  {name:'煤矿石', solid:true, tile:11, color:'#666a68'},
  {name:'铁矿石', solid:true, tile:12, color:'#a78c79'},
  {name:'水', solid:false, transparent:true, tile:13, color:'#488bb2'},
  {name:'基岩', solid:true, tile:14, color:'#464a48'},
];
export const BUILDABLE = [1,2,3,4,5,6,7,8,9,10,11];
export const DEFAULT_SLOTS = [1,2,3,4,5,7,8,9,6];
export const isSolid = id => Boolean(BLOCKS[id]?.solid);
export const inBounds = (x,y,z) => Number.isInteger(x) && Number.isInteger(y) && Number.isInteger(z) && x>=-256 && x<256 && z>=-256 && z<256 && y>=0 && y<96;
export const chunkKey = (cx,cz) => `${cx},${cz}`;
export const editKey = (x,y,z) => `${x},${y},${z}`;
