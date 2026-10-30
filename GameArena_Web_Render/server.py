import os, sqlite3, hashlib, secrets, hmac, datetime, threading
from flask import Flask, request, jsonify, render_template
from werkzeug.serving import run_simple
app=Flask(__name__)
ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.environ.get('GAMEARENA_DB',os.path.join(ROOT,'gamearena_online.db'))
LOCK=threading.RLock()
GAMES=['Fortnite','Rocket League','NHL','Call of Duty','Valorant','EA Sports FC','Minecraft']
def connect():
 c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
def init():
 with connect() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, salt TEXT NOT NULL, password_hash TEXT NOT NULL, points INTEGER DEFAULT 0); CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),expires TEXT NOT NULL); CREATE TABLE IF NOT EXISTS tournaments(id INTEGER PRIMARY KEY,title TEXT NOT NULL,game TEXT NOT NULL,owner_id INTEGER NOT NULL REFERENCES users(id),capacity INTEGER NOT NULL,created TEXT NOT NULL); CREATE TABLE IF NOT EXISTS entries(tournament_id INTEGER NOT NULL REFERENCES tournaments(id),user_id INTEGER NOT NULL REFERENCES users(id),PRIMARY KEY(tournament_id,user_id)); CREATE TABLE IF NOT EXISTS results(id INTEGER PRIMARY KEY,tournament_id INTEGER NOT NULL REFERENCES tournaments(id),player_id INTEGER NOT NULL REFERENCES users(id),score INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created TEXT NOT NULL,UNIQUE(tournament_id,player_id)); CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),body TEXT NOT NULL,created TEXT NOT NULL);''')
def init_v4():
 init()
 with connect() as c:
  c.executescript("""CREATE TABLE IF NOT EXISTS friendships(user_low INTEGER NOT NULL,user_high INTEGER NOT NULL,requested_by INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',PRIMARY KEY(user_low,user_high));
 CREATE TABLE IF NOT EXISTS teams(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL COLLATE NOCASE,owner_id INTEGER NOT NULL,created TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS team_members(team_id INTEGER NOT NULL,user_id INTEGER NOT NULL,PRIMARY KEY(team_id,user_id));
 CREATE TABLE IF NOT EXISTS direct_messages(id INTEGER PRIMARY KEY,sender_id INTEGER NOT NULL,recipient_id INTEGER NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS bracket_matches(id INTEGER PRIMARY KEY,tournament_id INTEGER NOT NULL,round INTEGER NOT NULL,slot INTEGER NOT NULL,player1 INTEGER,player2 INTEGER,score1 INTEGER,score2 INTEGER,winner INTEGER,UNIQUE(tournament_id,round,slot));""")
  columns=[r[1] for r in c.execute('PRAGMA table_info(tournaments)')]
  if 'state' not in columns:c.execute("ALTER TABLE tournaments ADD COLUMN state TEXT NOT NULL DEFAULT 'open'")

def pair(a,b):return (min(a,b),max(a,b))

def require_friend(c,a,b):
 lo,hi=pair(a,b)
 return c.execute("SELECT 1 FROM friendships WHERE user_low=? AND user_high=? AND status='accepted'",(lo,hi)).fetchone() is not None

@app.get('/api/users/search')
def users_search():
 u,e=need_auth()
 if e:return e
 q=request.args.get('q','').strip()
 if len(q)<2:return jsonify(users=[])
 with connect() as c:rows=c.execute('SELECT id,username FROM users WHERE username LIKE ? AND id!=? LIMIT 20',('%'+q+'%',u['id'])).fetchall()
 return jsonify(users=[dict(r) for r in rows])

@app.get('/api/friends')
def friends():
 u,e=need_auth()
 if e:return e
 with connect() as c:
  rows=c.execute("""SELECT f.status,f.requested_by,CASE WHEN f.user_low=? THEN f.user_high ELSE f.user_low END other_id, x.username FROM friendships f JOIN users x ON x.id=CASE WHEN f.user_low=? THEN f.user_high ELSE f.user_low END WHERE f.user_low=? OR f.user_high=? ORDER BY x.username""",(u['id'],u['id'],u['id'],u['id'])).fetchall()
 return jsonify(friends=[dict(r) for r in rows])

@app.post('/api/friends/request')
def friend_request():
 u,e=need_auth()
 if e:return e
 name=str(payload().get('username','')).strip()
 with LOCK,connect() as c:
  other=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
  if not other or other['id']==u['id']:return error('Joueur introuvable')
  lo,hi=pair(u['id'],other['id'])
  if c.execute('SELECT 1 FROM friendships WHERE user_low=? AND user_high=?',(lo,hi)).fetchone():return error('Demande déjà existante')
  c.execute('INSERT INTO friendships VALUES(?,?,?,?)',(lo,hi,u['id'],'pending'))
 return jsonify(ok=True)

@app.post('/api/friends/<int:other_id>/accept')
def accept_friend(other_id):
 u,e=need_auth()
 if e:return e
 lo,hi=pair(u['id'],other_id)
 with LOCK,connect() as c:
  cur=c.execute("UPDATE friendships SET status='accepted' WHERE user_low=? AND user_high=? AND requested_by=? AND status='pending'",(lo,hi,other_id))
  if not cur.rowcount:return error('Demande introuvable',404)
 return jsonify(ok=True)

@app.get('/api/teams')
def teams():
 u,e=need_auth()
 if e:return e
 with connect() as c:
  rows=c.execute("""SELECT t.id,t.name,x.username owner,(SELECT COUNT(*) FROM team_members m WHERE m.team_id=t.id) members FROM teams t JOIN users x ON x.id=t.owner_id ORDER BY t.id DESC""").fetchall()
 return jsonify(teams=[dict(r) for r in rows])

@app.post('/api/teams')
def create_team():
 u,e=need_auth()
 if e:return e
 name=str(payload().get('name','')).strip()
 if not 3<=len(name)<=36:return error('Nom de 3 à 36 caractères')
 try:
  with LOCK,connect() as c:
   cur=c.execute('INSERT INTO teams(name,owner_id,created) VALUES(?,?,?)',(name,u['id'],now().isoformat()))
   c.execute('INSERT INTO team_members VALUES(?,?)',(cur.lastrowid,u['id']))
 except sqlite3.IntegrityError:return error('Nom déjà utilisé',409)
 return jsonify(ok=True),201

@app.post('/api/teams/<int:team_id>/join')
def join_team(team_id):
 u,e=need_auth()
 if e:return e
 with LOCK,connect() as c:
  if not c.execute('SELECT 1 FROM teams WHERE id=?',(team_id,)).fetchone():return error('Équipe introuvable',404)
  if c.execute('SELECT 1 FROM team_members WHERE team_id=? AND user_id=?',(team_id,u['id'])).fetchone():return error('Déjà membre')
  c.execute('INSERT INTO team_members VALUES(?,?)',(team_id,u['id']))
 return jsonify(ok=True)

@app.get('/api/dm/<int:other_id>')
def dm_list(other_id):
 u,e=need_auth()
 if e:return e
 with connect() as c:
  if not require_friend(c,u['id'],other_id):return error('Amis requis',403)
  rows=c.execute("""SELECT m.body,m.created,x.username sender FROM direct_messages m JOIN users x ON x.id=m.sender_id WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?) ORDER BY m.id DESC LIMIT 100""",(u['id'],other_id,other_id,u['id'])).fetchall()
 return jsonify(messages=[dict(r) for r in reversed(rows)])

@app.post('/api/dm/<int:other_id>')
def dm_send(other_id):
 u,e=need_auth()
 if e:return e
 body=str(payload().get('body','')).strip()
 if not 1<=len(body)<=500:return error('Message vide ou trop long')
 with LOCK,connect() as c:
  if not require_friend(c,u['id'],other_id):return error('Amis requis',403)
  c.execute('INSERT INTO direct_messages(sender_id,recipient_id,body,created) VALUES(?,?,?,?)',(u['id'],other_id,body,now().strftime('%Y-%m-%d %H:%M UTC')))
 return jsonify(ok=True),201

@app.get('/api/tournaments/<int:tid>/bracket')
def bracket(tid):
 u,e=need_auth()
 if e:return e
 with connect() as c:
  t=c.execute('SELECT * FROM tournaments WHERE id=?',(tid,)).fetchone()
  if not t:return error('Tournoi introuvable',404)
  rows=c.execute("""SELECT b.*,a.username player1_name,z.username player2_name,w.username winner_name FROM bracket_matches b LEFT JOIN users a ON a.id=b.player1 LEFT JOIN users z ON z.id=b.player2 LEFT JOIN users w ON w.id=b.winner WHERE b.tournament_id=? ORDER BY b.round,b.slot""",(tid,)).fetchall()
 return jsonify(state=t['state'],owner=t['owner_id']==u['id'],matches=[dict(r) for r in rows])

@app.post('/api/tournaments/<int:tid>/start')
def start_bracket(tid):
 u,e=need_auth()
 if e:return e
 with LOCK,connect() as c:
  t=c.execute('SELECT * FROM tournaments WHERE id=?',(tid,)).fetchone()
  if not t:return error('Tournoi introuvable',404)
  if t['owner_id']!=u['id']:return error('Organisateur requis',403)
  if t['state']!='open':return error('Tournoi déjà commencé')
  players=[r[0] for r in c.execute('SELECT user_id FROM entries WHERE tournament_id=? ORDER BY user_id',(tid,))]
  if len(players)<2:return error('Il faut au moins 2 inscrits')
  size=1
  while size<len(players):size*=2
  players += [None]*(size-len(players))
  for i in range(size//2):
   a,b=players[2*i:2*i+2]
   c.execute('INSERT INTO bracket_matches(tournament_id,round,slot,player1,player2) VALUES(?,?,?,?,?)',(tid,1,i,a,b))
  c.execute("UPDATE tournaments SET state='active' WHERE id=?",(tid,))
  advance_byes(c,tid)
 return jsonify(ok=True)

def advance_byes(c,tid):
 while True:
  rows=c.execute('SELECT * FROM bracket_matches WHERE tournament_id=? ORDER BY round,slot',(tid,)).fetchall()
  changed=False
  for m in rows:
   if m['winner'] is not None:continue
   if m['player1'] is not None and m['player2'] is None:
    # Only award a bye in the first round; later rounds may await an opponent.
    if m['round']==1:
     c.execute('UPDATE bracket_matches SET winner=? WHERE id=?',(m['player1'],m['id']))
     advance_winner(c,tid,m['round'],m['slot'],m['player1']);changed=True
  if not changed:break

def advance_winner(c,tid,round_num,slot,winner):
 count=c.execute('SELECT COUNT(*) FROM bracket_matches WHERE tournament_id=? AND round=?',(tid,round_num)).fetchone()[0]
 if count==1:
  c.execute("UPDATE tournaments SET state='finished' WHERE id=?",(tid,));return
 next_slot=slot//2
 c.execute('INSERT OR IGNORE INTO bracket_matches(tournament_id,round,slot) VALUES(?,?,?)',(tid,round_num+1,next_slot))
 column='player1' if slot%2==0 else 'player2'
 c.execute(f'UPDATE bracket_matches SET {column}=? WHERE tournament_id=? AND round=? AND slot=?',(winner,tid,round_num+1,next_slot))

@app.post('/api/matches/<int:match_id>/report')
def report_match(match_id):
 u,e=need_auth()
 if e:return e
 d=payload()
 try:a,b=int(d.get('score1')),int(d.get('score2'))
 except (TypeError,ValueError):return error('Scores invalides')
 if not (0<=a<=999 and 0<=b<=999) or a==b:return error('Scores différents requis (0-999)')
 with LOCK,connect() as c:
  m=c.execute("""SELECT b.*,t.owner_id,t.state FROM bracket_matches b JOIN tournaments t ON t.id=b.tournament_id WHERE b.id=?""",(match_id,)).fetchone()
  if not m:return error('Match introuvable',404)
  if m['owner_id']!=u['id']:return error('Organisateur requis',403)
  if m['state']!='active' or m['winner'] is not None or m['player1'] is None or m['player2'] is None:return error('Match non disponible')
  winner=m['player1'] if a>b else m['player2']
  c.execute('UPDATE bracket_matches SET score1=?,score2=?,winner=? WHERE id=?',(a,b,winner,match_id))
  advance_winner(c,m['tournament_id'],m['round'],m['slot'],winner)
 return jsonify(ok=True)

def now():return datetime.datetime.now(datetime.timezone.utc)
def error(msg,status=400):return jsonify(error=msg),status
def payload():return request.get_json(silent=True) or {}
def hashpass(password,salt):return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),260000).hex()
def auth():
 raw=request.headers.get('Authorization','')
 if not raw.startswith('Bearer '):return None
 token=raw[7:];digest=hashlib.sha256(token.encode()).hexdigest()
 with connect() as c:
  return c.execute('SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token_hash=? AND s.expires>?',(digest,now().isoformat())).fetchone()
def need_auth():
 u=auth()
 return u,None if u else error('Connexion requise',401)
@app.get('/')
def index():return render_template('index.html')
@app.get('/api/health')
def health():return jsonify(ok=True,version='4.0',mode='online')
@app.post('/api/register')
def register():
 d=payload();name=str(d.get('username','')).strip();pw=str(d.get('password',''))
 if not (3<=len(name)<=24 and name.replace('_','').isalnum()):return error('Pseudo : 3 à 24 lettres/chiffres ou _')
 if len(pw)<10:return error('Mot de passe : au moins 10 caractères')
 salt=secrets.token_hex(16)
 try:
  with LOCK,connect() as c:c.execute('INSERT INTO users(username,salt,password_hash) VALUES(?,?,?)',(name,salt,hashpass(pw,salt)))
 except sqlite3.IntegrityError:return error('Pseudo déjà utilisé',409)
 return jsonify(ok=True),201
@app.post('/api/login')
def login():
 d=payload();name=str(d.get('username','')).strip();pw=str(d.get('password',''))
 with connect() as c:u=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone()
 if not u or not hmac.compare_digest(u['password_hash'],hashpass(pw,u['salt'])):return error('Identifiants invalides',401)
 token=secrets.token_urlsafe(40)
 with LOCK,connect() as c:c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],(now()+datetime.timedelta(days=7)).isoformat()))
 return jsonify(token=token,username=u['username'])
@app.post('/api/logout')
def logout():
 u,e=need_auth()
 if e:return e
 raw=request.headers.get('Authorization','')[7:]
 with LOCK,connect() as c:c.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(raw.encode()).hexdigest(),))
 return jsonify(ok=True)
@app.get('/api/me')
def me():
 u,e=need_auth()
 return e if e else jsonify(id=u['id'],username=u['username'],points=u['points'])
@app.get('/api/tournaments')
def tournaments():
 with connect() as c:
  rows=c.execute('''SELECT t.id,t.title,t.game,t.capacity,t.owner_id,u.username owner,(SELECT COUNT(*) FROM entries e WHERE e.tournament_id=t.id) joined FROM tournaments t JOIN users u ON u.id=t.owner_id ORDER BY t.id DESC''').fetchall()
 return jsonify(tournaments=[dict(r) for r in rows])
@app.post('/api/tournaments')
def create():
 u,e=need_auth()
 if e:return e
 d=payload();title=str(d.get('title','')).strip();game=d.get('game');capacity=d.get('capacity',8)
 try:capacity=int(capacity)
 except (TypeError,ValueError):return error('Capacité invalide')
 if not 3<=len(title)<=70 or game not in GAMES or not 2<=capacity<=128:return error('Titre, jeu ou capacité invalide')
 with LOCK,connect() as c:
  cur=c.execute('INSERT INTO tournaments(title,game,owner_id,capacity,created) VALUES(?,?,?,?,?)',(title,game,u['id'],capacity,now().isoformat()));tid=cur.lastrowid
  c.execute('INSERT INTO entries VALUES(?,?)',(tid,u['id']))
 return jsonify(id=tid),201
@app.post('/api/tournaments/<int:tid>/join')
def join(tid):
 u,e=need_auth()
 if e:return e
 with LOCK,connect() as c:
  t=c.execute('SELECT capacity,state FROM tournaments WHERE id=?',(tid,)).fetchone()
  if not t:return error('Tournoi introuvable',404)
  if t['state']!='open':return error('Inscriptions fermées')
  if c.execute('SELECT 1 FROM entries WHERE tournament_id=? AND user_id=?',(tid,u['id'])).fetchone():return error('Déjà inscrit')
  if c.execute('SELECT COUNT(*) FROM entries WHERE tournament_id=?',(tid,)).fetchone()[0]>=t['capacity']:return error('Tournoi complet')
  c.execute('INSERT INTO entries VALUES(?,?)',(tid,u['id']))
 return jsonify(ok=True)
@app.get('/api/tournaments/<int:tid>/results')
def results(tid):
 u,e=need_auth()
 if e:return e
 with connect() as c:
  t=c.execute('SELECT owner_id FROM tournaments WHERE id=?',(tid,)).fetchone()
  if not t:return error('Tournoi introuvable',404)
  rows=c.execute('SELECT r.id,r.score,r.status,u.username FROM results r JOIN users u ON u.id=r.player_id WHERE r.tournament_id=? ORDER BY r.id DESC',(tid,)).fetchall()
 return jsonify(owner=(t['owner_id']==u['id']),results=[dict(r) for r in rows])
@app.post('/api/tournaments/<int:tid>/results')
def submit(tid):
 u,e=need_auth()
 if e:return e
 try:score=int(payload().get('score'))
 except (ValueError,TypeError):return error('Score invalide')
 if not 0<=score<=99999:return error('Score hors limite')
 with LOCK,connect() as c:
  if not c.execute('SELECT 1 FROM entries WHERE tournament_id=? AND user_id=?',(tid,u['id'])).fetchone():return error('Inscription requise',403)
  if c.execute('SELECT 1 FROM results WHERE tournament_id=? AND player_id=?',(tid,u['id'])).fetchone():return error('Résultat déjà soumis')
  c.execute('INSERT INTO results(tournament_id,player_id,score,created) VALUES(?,?,?,?)',(tid,u['id'],score,now().isoformat()))
 return jsonify(ok=True),201
@app.post('/api/results/<int:rid>/approve')
def approve(rid):
 u,e=need_auth()
 if e:return e
 with LOCK,connect() as c:
  r=c.execute('SELECT r.*,t.owner_id FROM results r JOIN tournaments t ON t.id=r.tournament_id WHERE r.id=?',(rid,)).fetchone()
  if not r:return error('Résultat introuvable',404)
  if r['owner_id']!=u['id']:return error('Réservé à l’organisateur',403)
  if r['status']!='pending':return error('Résultat déjà traité')
  c.execute("UPDATE results SET status='approved' WHERE id=?",(rid,));c.execute('UPDATE users SET points=points+? WHERE id=?',(r['score'],r['player_id']))
 return jsonify(ok=True)
@app.get('/api/rankings')
def rankings():
 with connect() as c:rows=c.execute('SELECT username,points FROM users ORDER BY points DESC,username LIMIT 100').fetchall()
 return jsonify(players=[dict(r) for r in rows])
@app.get('/api/messages')
def messages():
 u,e=need_auth()
 if e:return e
 with connect() as c:rows=c.execute('SELECT u.username,m.body,m.created FROM messages m JOIN users u ON u.id=m.user_id ORDER BY m.id DESC LIMIT 70').fetchall()
 return jsonify(messages=[dict(r) for r in reversed(rows)])
@app.post('/api/messages')
def send():
 u,e=need_auth()
 if e:return e
 body=str(payload().get('body','')).strip()
 if not 1<=len(body)<=500:return error('Message vide ou trop long')
 with LOCK,connect() as c:c.execute('INSERT INTO messages(user_id,body,created) VALUES(?,?,?)',(u['id'],body,now().strftime('%Y-%m-%d %H:%M UTC')))
 return jsonify(ok=True),201
if __name__=='__main__':
 init_v4();host=os.environ.get('GAMEARENA_HOST','127.0.0.1');port=int(os.environ.get('GAMEARENA_PORT','5055'));print('GameArena server:',host,port);run_simple(host,port,app,threaded=True)
