from flask import Flask, request, jsonify, render_template, Response
import sqlite3, os, json, secrets, re, csv, io
from datetime import datetime, timezone
app=Flask(__name__)
DB=os.path.join(os.path.dirname(__file__),'classflow.db')
ADMIN=os.environ.get('CLASSFLOW_ADMIN_CODE','demo-teacher')

def db():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c

def init():
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, code TEXT UNIQUE, title TEXT, active INTEGER DEFAULT 1, challenge INTEGER DEFAULT 0, feedback_open INTEGER DEFAULT 1, photo TEXT DEFAULT '', pins TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS attendance(session_id INTEGER, student TEXT, PRIMARY KEY(session_id,student));
CREATE TABLE IF NOT EXISTS claims(session_id INTEGER, student TEXT, pin INTEGER, PRIMARY KEY(session_id,student));
CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, student TEXT, text TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS polls(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, question TEXT, options TEXT, open INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS votes(poll_id INTEGER, student TEXT, option INTEGER, PRIMARY KEY(poll_id,student));
CREATE TABLE IF NOT EXISTS resolutions(session_id INTEGER, pin INTEGER, note TEXT, PRIMARY KEY(session_id,pin));''')
  if not c.execute('SELECT 1 FROM sessions').fetchone():c.execute('INSERT INTO sessions(code,title) VALUES(?,?)',('DEMO101','שיעור לדוגמה'))
init()
def err(message,status=400):return jsonify(error=message),status
def get_session(code):
 with db() as c:return c.execute('SELECT * FROM sessions WHERE code=?',(code,)).fetchone()
def admin(data):return secrets.compare_digest(str(data.get('admin','')),ADMIN)
def student(data):
 v=str(data.get('student','')).strip()
 return v if 3<=len(v)<=32 and all(x.isalnum() or x in '-_' for x in v) else None
@app.get('/')
def index():return render_template('index.html')
@app.get('/api/state')
def state():
 code=request.args.get('code','DEMO101').strip().upper();s=get_session(code)
 if not s:return err('שיעור לא נמצא',404)
 with db() as c:
  attendees=[x['student'] for x in c.execute('SELECT student FROM attendance WHERE session_id=?',(s['id'],))]
  claims={x['student']:x['pin'] for x in c.execute('SELECT student,pin FROM claims WHERE session_id=?',(s['id'],))}
  notes=[dict(x) for x in c.execute('SELECT student,text,created FROM feedback WHERE session_id=? ORDER BY id DESC LIMIT 100',(s['id'],))]
  poll=c.execute('SELECT * FROM polls WHERE session_id=? ORDER BY id DESC LIMIT 1',(s['id'],)).fetchone()
  p=None
  if poll:
   votes={x['student']:x['option'] for x in c.execute('SELECT student,option FROM votes WHERE poll_id=?',(poll['id'],))}
   p=dict(question=poll['question'],options=json.loads(poll['options']),open=bool(poll['open']),votes=votes)
  resolutions={str(x['pin']):x['note'] for x in c.execute('SELECT pin,note FROM resolutions WHERE session_id=?',(s['id'],))}
 return jsonify(code=code,title=s['title'],students=attendees,claims=claims,feedback=notes,poll=p,photo=s['photo'],pins=json.loads(s['pins']),challenge=bool(s['challenge']),feedback_open=bool(s['feedback_open']),resolutions=resolutions)
def keywords(text):
 words=re.findall(r"[\w\u0590-\u05ff]+", text.lower(), re.UNICODE)
 stop={'the','and','for','with','this','that','של','את','על','אני','הוא','היא','זה','הם','גם','אבל','עם','יש','אין','מה','איך','שלנו'}
 return [w for w in words if len(w)>=3 and w not in stop]

@app.post('/api/analyze')
def analyze():
 d=request.get_json(silent=True) or {}
 if not admin(d):return err('קוד מרצה שגוי',403)
 kind=d.get('kind');content=str(d.get('content',''))
 if len(content)>25000:return err('טקסט ארוך מדי')
 if kind=='summary':
  sentences=[x.strip() for x in re.split(r'(?<=[.!?])\s+|\n+',content) if x.strip()]
  if not sentences:return err('יש להזין תמלול')
  freq={}
  for w in keywords(content):freq[w]=freq.get(w,0)+1
  ranked=sorted(enumerate(sentences),key=lambda it:(-sum(freq.get(w,0) for w in set(keywords(it[1]))),it[0]))
  chosen=sorted(ranked[:min(4,len(ranked))],key=lambda x:x[0])
  top=sorted(freq,key=lambda w:(-freq[w],w))[:8]
  return jsonify(summary=[x[1] for x in chosen],topics=top,method='extractive_frequency',ai=False)
 if kind=='rubric':
  criteria=[x.strip() for x in str(d.get('criteria','')).splitlines() if x.strip()][:15]
  if not criteria or not content.strip():return err('יש להזין תשובה וקריטריונים')
  results=[]
  words=set(keywords(content))
  for criterion in criteria:
   tokens=set(keywords(criterion));overlap=sorted(tokens & words)
   results.append({'criterion':criterion,'matched':overlap,'coverage':round(len(overlap)/len(tokens)*100) if tokens else 0})
  return jsonify(results=results,method='keyword_overlap',ai=False,warning='אינדיקציה מילולית בלבד; לא בדיקת נכונות או ציון')
 return err('סוג ניתוח לא מוכר')

@app.get('/api/export')
def export():
 code=request.args.get('code','DEMO101').upper();s=get_session(code)
 if not s:return err('שיעור לא נמצא',404)
 if not secrets.compare_digest(request.args.get('admin',''),ADMIN):return err('אין הרשאה',403)
 out=io.StringIO();writer=csv.writer(out);writer.writerow(['student','registered','claimed_pin','claimants_on_pin','status'])
 with db() as c:
  students=[x['student'] for x in c.execute('SELECT student FROM attendance WHERE session_id=? ORDER BY student',(s['id'],))]
  claims={x['student']:x['pin'] for x in c.execute('SELECT student,pin FROM claims WHERE session_id=?',(s['id'],))}
  counts={}
  for pin in claims.values():counts[pin]=counts.get(pin,0)+1
  for st in students:
   pin=claims.get(st);n=counts.get(pin,0) if pin is not None else 0
   writer.writerow([st,'yes',pin+1 if pin is not None else '',n,'conflict' if n>1 else 'claimed' if n==1 else 'unverified'])
 return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=attendance.csv','Cache-Control':'no-store'})

@app.get('/api/report')
def report():
 code=request.args.get('code','DEMO101').strip().upper();s=get_session(code)
 if not s:return err('שיעור לא נמצא',404)
 if not secrets.compare_digest(request.args.get('admin',''),ADMIN):return err('אין הרשאה',403)
 with db() as c:
  students=[x['student'] for x in c.execute('SELECT student FROM attendance WHERE session_id=? ORDER BY student',(s['id'],))]
  claims={x['student']:x['pin'] for x in c.execute('SELECT student,pin FROM claims WHERE session_id=?',(s['id'],))}
  resolutions={x['pin']:x['note'] for x in c.execute('SELECT pin,note FROM resolutions WHERE session_id=?',(s['id'],))}
 counts={}
 for pin in claims.values():counts[pin]=counts.get(pin,0)+1
 rows=[]
 for st in students:
  pin=claims.get(st)
  status='לא בחר מיקום' if pin is None else ('התנגשות - נדרש בירור' if counts[pin]>1 else 'בחירה יחידה - לא מאומת')
  rows.append(dict(student=st,pin=pin+1 if pin is not None else None,status=status,review=resolutions.get(pin,'')))
 return jsonify(registered=len(students),marked=len(json.loads(s['pins'])),claimed=len(claims),conflicts=sum(n>1 for n in counts.values()),students=rows,warning='סימון בתמונה אינו אימות זהות או הוכחת נוכחות; לא ניתן לקבוע מי נעדר ללא רשימת כיתה ואימות נוסף.')

@app.post('/api/create_session')
def create_session():
 d=request.get_json(silent=True) or {}
 if not admin(d):return err('קוד מרצה שגוי',403)
 code=str(d.get('new_code','')).strip().upper()
 title=str(d.get('title','')).strip()
 if not re.fullmatch(r'[A-Z0-9_-]{4,20}',code):return err('קוד שיעור חייב להכיל 4 עד 20 תווים באנגלית, ספרות, _ או -')
 if not 1<=len(title)<=100:return err('יש להזין שם שיעור (עד 100 תווים)')
 with db() as c:
  if c.execute('SELECT 1 FROM sessions WHERE code=?',(code,)).fetchone():return err('קוד שיעור כבר קיים',409)
  c.execute('INSERT INTO sessions(code,title) VALUES(?,?)',(code,title))
 return jsonify(ok=True,code=code,title=title),201

@app.post('/api/action')
def action():
 d=request.get_json(silent=True) or {};code=str(d.get('code','DEMO101')).upper();s=get_session(code)
 if not s:return err('שיעור לא נמצא',404)
 a=d.get('action');sid=s['id'];st=student(d);is_admin=admin(d)
 lecturer={'photo','pins','challenge','resolve','poll','close_poll','feedback_open','new_session'}
 if a in lecturer and not is_admin:return err('קוד מרצה שגוי',403)
 if a in {'register','claim','vote','feedback'} and not st:return err('מזהה סטודנט לא תקין')
 with db() as c:
  registered=bool(st and c.execute('SELECT 1 FROM attendance WHERE session_id=? AND student=?',(sid,st)).fetchone())
  if a=='register':c.execute('INSERT OR IGNORE INTO attendance VALUES(?,?)',(sid,st))
  elif a in {'claim','vote','feedback'} and not registered:return err('יש להירשם תחילה')
  elif a=='photo':
   photo=str(d.get('photo',''))
   if not photo.startswith('data:image/jpeg;base64,') or len(photo)>1700000:return err('תמונה לא תקינה או גדולה מדי')
   c.execute('UPDATE sessions SET photo=?,pins=?,challenge=0 WHERE id=?',(photo,'[]',sid));c.execute('DELETE FROM claims WHERE session_id=?',(sid,));c.execute('DELETE FROM resolutions WHERE session_id=?',(sid,))
  elif a=='pins':
   pins=d.get('pins',[])
   if not isinstance(pins,list) or len(pins)>300 or any(not isinstance(p,dict) or not all(isinstance(p.get(k),(int,float)) and 0<=p[k]<=100 for k in ('x','y')) for p in pins):return err('סימונים לא תקינים')
   c.execute('UPDATE sessions SET pins=? WHERE id=?',(json.dumps(pins),sid));c.execute('DELETE FROM claims WHERE session_id=?',(sid,));c.execute('DELETE FROM resolutions WHERE session_id=?',(sid,))
  elif a=='challenge':
   if d.get('value') and (not s['photo'] or not json.loads(s['pins'])):return err('צריך תמונה וסימונים')
   c.execute('UPDATE sessions SET challenge=? WHERE id=?',(int(bool(d.get('value'))),sid))
  elif a=='claim':
   pin=d.get('pin');pins=json.loads(s['pins'])
   if not s['challenge'] or type(pin)!=int or not 0<=pin<len(pins):return err('אתגר לא פעיל או סימון לא תקין')
   c.execute('INSERT INTO claims VALUES(?,?,?) ON CONFLICT(session_id,student) DO UPDATE SET pin=excluded.pin',(sid,st,pin))
  elif a=='feedback':
   t=str(d.get('text','')).strip()
   if not s['feedback_open'] or not 1<=len(t)<=1000:return err('הערות סגורות או טקסט לא תקין')
   c.execute('INSERT INTO feedback(session_id,student,text,created) VALUES(?,?,?,?)',(sid,st,t,datetime.now(timezone.utc).isoformat(timespec='seconds')))
  elif a=='feedback_open':c.execute('UPDATE sessions SET feedback_open=? WHERE id=?',(int(bool(d.get('value'))),sid))
  elif a=='poll':
   q=str(d.get('question','')).strip();opts=d.get('options',[])
   if not 1<=len(q)<=300 or not isinstance(opts,list) or not 2<=len(opts)<=8 or any(not isinstance(x,str) or not 1<=len(x.strip())<=150 for x in opts):return err('שאלה או אפשרויות לא תקינות')
   c.execute('UPDATE polls SET open=0 WHERE session_id=?',(sid,));c.execute('INSERT INTO polls(session_id,question,options) VALUES(?,?,?)',(sid,q,json.dumps(opts,ensure_ascii=False)))
  elif a=='close_poll':c.execute('UPDATE polls SET open=0 WHERE session_id=?',(sid,))
  elif a=='vote':
   p=c.execute('SELECT * FROM polls WHERE session_id=? ORDER BY id DESC LIMIT 1',(sid,)).fetchone();option=d.get('option')
   if not p or not p['open'] or type(option)!=int or not 0<=option<len(json.loads(p['options'])):return err('סקר לא פעיל')
   c.execute('INSERT INTO votes VALUES(?,?,?) ON CONFLICT(poll_id,student) DO UPDATE SET option=excluded.option',(p['id'],st,option))
  elif a=='resolve':
   pin=d.get('pin');note=str(d.get('note','')).strip()
   if type(pin)!=int or not 0<=pin<len(json.loads(s['pins'])) or not 1<=len(note)<=250:return err('החלטה לא תקינה')
   c.execute('INSERT INTO resolutions VALUES(?,?,?) ON CONFLICT(session_id,pin) DO UPDATE SET note=excluded.note',(sid,pin,note))
  elif a=='new_session':
   for table in ('attendance','claims','feedback','polls','resolutions'):c.execute(f'DELETE FROM {table} WHERE session_id=?',(sid,))
   c.execute('DELETE FROM votes WHERE poll_id NOT IN (SELECT id FROM polls)')
   c.execute("UPDATE sessions SET photo='',pins='[]',challenge=0,feedback_open=1 WHERE id=?",(sid,))
  else:return err('פעולה לא מוכרת')
 return jsonify(ok=True)
if __name__=='__main__':
 print('Open http://127.0.0.1:5000 ; demo lecturer code:',ADMIN)
 app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=False)
