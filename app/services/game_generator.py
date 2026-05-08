"""HTML5 game generator — creates self-contained browser games."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

_SNAKE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Snake</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:Arial,sans-serif;color:#fff}
h1{margin-bottom:10px;color:#0f3460}
#score{font-size:20px;margin-bottom:10px;color:#e94560}
canvas{border:2px solid #0f3460;background:#16213e}
#msg{margin-top:10px;font-size:16px;color:#e94560;height:20px}
</style></head>
<body>
<h1>🐍 Snake</h1>
<div id="score">Score: 0</div>
<canvas id="c" width="400" height="400"></canvas>
<div id="msg">Press any arrow key to start</div>
<script>
const cv=document.getElementById('c'),ctx=cv.getContext('2d'),SZ=20,W=20,H=20;
let snake=[{x:10,y:10}],dir={x:0,y:0},food=rndFood(),score=0,running=false,loop;
function rndFood(){return{x:Math.floor(Math.random()*W),y:Math.floor(Math.random()*H)};}
function draw(){
  ctx.fillStyle='#16213e';ctx.fillRect(0,0,400,400);
  ctx.fillStyle='#e94560';ctx.fillRect(food.x*SZ,food.y*SZ,SZ-2,SZ-2);
  snake.forEach((s,i)=>{ctx.fillStyle=i===0?'#0f3460':'#1a4a8a';ctx.fillRect(s.x*SZ,s.y*SZ,SZ-2,SZ-2);});
}
function tick(){
  if(!dir.x&&!dir.y)return;
  const h={x:snake[0].x+dir.x,y:snake[0].y+dir.y};
  if(h.x<0||h.x>=W||h.y<0||h.y>=H||snake.some(s=>s.x===h.x&&s.y===h.y)){
    document.getElementById('msg').textContent='Game Over! Score: '+score+' — Reload to restart';
    clearInterval(loop);return;
  }
  snake.unshift(h);
  if(h.x===food.x&&h.y===food.y){score++;document.getElementById('score').textContent='Score: '+score;food=rndFood();}
  else snake.pop();
  draw();
}
document.addEventListener('keydown',e=>{
  const m={ArrowUp:{x:0,y:-1},ArrowDown:{x:0,y:1},ArrowLeft:{x:-1,y:0},ArrowRight:{x:1,y:0}};
  if(m[e.key]&&!(dir.x===-m[e.key].x||dir.y===-m[e.key].y)){dir=m[e.key];if(!running){running=true;loop=setInterval(tick,120);document.getElementById('msg').textContent='';}}
  e.preventDefault();
});
draw();
</script></body></html>"""

_TICTACTOE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Tic-Tac-Toe</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:Arial,sans-serif;color:#fff}
h1{margin-bottom:20px;color:#e94560}
#board{display:grid;grid-template-columns:repeat(3,120px);gap:4px;background:#0f3460;padding:4px}
.cell{width:120px;height:120px;background:#16213e;display:flex;align-items:center;justify-content:center;font-size:60px;cursor:pointer;transition:.2s}
.cell:hover{background:#1a4a8a}
#status{margin-top:20px;font-size:22px;height:30px;color:#e94560}
button{margin-top:20px;padding:10px 30px;background:#e94560;color:#fff;border:none;border-radius:6px;font-size:16px;cursor:pointer}
button:hover{background:#c73652}
</style></head>
<body>
<h1>❌ Tic-Tac-Toe ⭕</h1>
<div id="board"></div>
<div id="status">Player X's turn</div>
<button onclick="reset()">New Game</button>
<script>
let board=Array(9).fill(''),cur='X',over=false;
const wins=[[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];
function render(){
  const b=document.getElementById('board');b.innerHTML='';
  board.forEach((v,i)=>{const c=document.createElement('div');c.className='cell';c.textContent=v;c.onclick=()=>move(i);b.appendChild(c);});
}
function move(i){
  if(board[i]||over)return;
  board[i]=cur;
  const w=wins.find(([a,b,c])=>board[a]&&board[a]===board[b]&&board[a]===board[c]);
  if(w){document.getElementById('status').textContent='Player '+cur+' wins! 🎉';over=true;}
  else if(board.every(v=>v)){document.getElementById('status').textContent="It's a draw!";over=true;}
  else{cur=cur==='X'?'O':'X';document.getElementById('status').textContent="Player "+cur+"'s turn";}
  render();
}
function reset(){board=Array(9).fill('');cur='X';over=false;document.getElementById('status').textContent="Player X's turn";render();}
render();
</script></body></html>"""

_MEMORY_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Memory Game</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:Arial,sans-serif;color:#fff;padding:20px}
h1{margin-bottom:15px;color:#e94560}
#info{display:flex;gap:30px;margin-bottom:15px;font-size:18px}
#board{display:grid;grid-template-columns:repeat(4,90px);gap:8px}
.card{width:90px;height:90px;background:#0f3460;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:40px;cursor:pointer;transition:.3s;user-select:none}
.card.flipped,.card.matched{background:#16213e;border:2px solid #e94560}
.card.matched{background:#0d4a1a;border-color:#4caf50;cursor:default}
button{margin-top:20px;padding:10px 30px;background:#e94560;color:#fff;border:none;border-radius:6px;font-size:16px;cursor:pointer}
</style></head>
<body>
<h1>🧠 Memory Game</h1>
<div id="info"><span>Moves: <b id="moves">0</b></span><span>Pairs: <b id="pairs">0</b>/8</span></div>
<div id="board"></div>
<button onclick="init()">New Game</button>
<script>
const emojis=['🍕','🎸','🚀','🐶','🌈','⚡','🎯','🏆'];
let cards=[],flipped=[],matched=0,moves=0,lock=false;
function shuffle(a){for(let i=a.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]];}}
function init(){
  cards=[...emojis,...emojis];shuffle(cards);flipped=[];matched=0;moves=0;lock=false;
  document.getElementById('moves').textContent=0;document.getElementById('pairs').textContent=0;
  const b=document.getElementById('board');b.innerHTML='';
  cards.forEach((e,i)=>{
    const d=document.createElement('div');d.className='card';d.dataset.i=i;d.dataset.e=e;
    d.onclick=()=>flip(d);b.appendChild(d);
  });
}
function flip(d){
  if(lock||d.classList.contains('flipped')||d.classList.contains('matched'))return;
  d.classList.add('flipped');d.textContent=d.dataset.e;flipped.push(d);
  if(flipped.length===2){
    moves++;document.getElementById('moves').textContent=moves;lock=true;
    setTimeout(()=>{
      if(flipped[0].dataset.e===flipped[1].dataset.e){
        flipped.forEach(c=>c.classList.replace('flipped','matched'));
        matched++;document.getElementById('pairs').textContent=matched;
        if(matched===8)setTimeout(()=>alert('You won in '+moves+' moves!'),100);
      }else{flipped.forEach(c=>{c.classList.remove('flipped');c.textContent='';})}
      flipped=[];lock=false;
    },700);
  }
}
init();
</script></body></html>"""

_2048_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>2048</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#faf8ef;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;font-family:'Arial Rounded MT Bold',Arial,sans-serif}
h1{font-size:60px;color:#776e65;margin-bottom:10px}
#score-box{background:#bbada0;color:#f9f6f2;font-size:20px;padding:8px 20px;border-radius:6px;margin-bottom:15px}
#board{background:#bbada0;padding:8px;border-radius:8px;display:grid;grid-template-columns:repeat(4,100px);gap:8px}
.cell{width:100px;height:100px;background:#cdc1b4;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:28px;font-weight:bold;color:#776e65}
.c2{background:#eee4da}.c4{background:#ede0c8}.c8{background:#f2b179;color:#f9f6f2}
.c16{background:#f59563;color:#f9f6f2}.c32{background:#f67c5f;color:#f9f6f2}.c64{background:#f65e3b;color:#f9f6f2}
.c128{background:#edcf72;color:#f9f6f2;font-size:22px}.c256{background:#edcc61;color:#f9f6f2;font-size:22px}
.c512{background:#edc850;color:#f9f6f2;font-size:22px}.c1024{background:#edc53f;color:#f9f6f2;font-size:18px}
.c2048{background:#edc22e;color:#f9f6f2;font-size:18px}
button{margin-top:20px;padding:10px 30px;background:#8f7a66;color:#f9f6f2;border:none;border-radius:6px;font-size:16px;cursor:pointer}
</style></head>
<body>
<h1>2048</h1>
<div id="score-box">Score: <span id="score">0</span></div>
<div id="board"></div>
<button onclick="init()">New Game</button>
<script>
let g=Array(16).fill(0),score=0;
const colors={2:'c2',4:'c4',8:'c8',16:'c16',32:'c32',64:'c64',128:'c128',256:'c256',512:'c512',1024:'c1024',2048:'c2048'};
function addRnd(){const e=g.map((v,i)=>v===0?i:-1).filter(i=>i>=0);if(!e.length)return;g[e[Math.floor(Math.random()*e.length)]]=Math.random()<0.9?2:4;}
function render(){document.getElementById('score').textContent=score;const b=document.getElementById('board');b.innerHTML='';g.forEach(v=>{const c=document.createElement('div');c.className='cell '+(v?colors[v]||'c2048':'');c.textContent=v||'';b.appendChild(c);});}
function slide(row){let r=row.filter(v=>v);for(let i=0;i<r.length-1;i++)if(r[i]===r[i+1]){score+=r[i]*2;r[i]*=2;r.splice(i+1,1);}while(r.length<4)r.push(0);return r;}
function move(d){let ng=g.slice(),changed=false;
  for(let i=0;i<4;i++){let row;if(d==='l')row=ng.slice(i*4,i*4+4);else if(d==='r')row=ng.slice(i*4,i*4+4).reverse();else if(d==='u')row=[ng[i],ng[i+4],ng[i+8],ng[i+12]];else row=[ng[i+12],ng[i+8],ng[i+4],ng[i]];
  const s=slide(row);if(d==='l')ng.splice(i*4,4,...s);else if(d==='r')ng.splice(i*4,4,...s.reverse());else if(d==='u')[ng[i],ng[i+4],ng[i+8],ng[i+12]]=s;else[ng[i+12],ng[i+8],ng[i+4],ng[i]]=s;}
  if(ng.join('')!==g.join('')){changed=true;g=ng;}if(changed){addRnd();render();}}
function init(){g=Array(16).fill(0);score=0;addRnd();addRnd();render();}
document.addEventListener('keydown',e=>{const m={ArrowLeft:'l',ArrowRight:'r',ArrowUp:'u',ArrowDown:'d'};if(m[e.key]){move(m[e.key]);e.preventDefault();}});
init();
</script></body></html>"""

GAMES: dict[str, str] = {
    "snake": _SNAKE_HTML,
    "tictactoe": _TICTACTOE_HTML,
    "memory": _MEMORY_HTML,
    "2048": _2048_HTML,
}

GAME_LABELS = {
    "snake": "Змейка",
    "tictactoe": "Крестики-нолики",
    "memory": "Игра память",
    "2048": "2048",
}


def list_games() -> List[str]:
    return list(GAMES.keys())


def generate_game(game_type: str, output_dir: str = "state/games") -> str:
    """Generate HTML5 game file. Returns path to created file."""
    if game_type not in GAMES:
        available = ", ".join(GAMES.keys())
        raise ValueError(f"Unknown game: '{game_type}'. Available: {available}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir) / f"{game_type}_{ts}.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(GAMES[game_type], encoding="utf-8")

    return str(output_path)
