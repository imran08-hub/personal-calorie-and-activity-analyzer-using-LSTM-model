from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import sqlite3, hashlib, json, os, re
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None
from datetime import datetime, date, timedelta
import numpy as np
import urllib.request, urllib.parse
import requests

app = Flask(__name__)
app.secret_key = 'nutriai_v3_secret_2024'
DB_PATH = 'nutriai.db'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ======================================================================
# NUTRITION API (Edamam - requires App ID + API Key)
# ==============================================================================
# Get your free developer keys at: https://developer.edamam.com/
EDAMAM_APP_ID = "2781692f"
EDAMAM_APP_KEY = "414216cbc61b8baddf459b74cf6fc147"

# USDA API Key (Using DEMO_KEY by default)
USDA_API_KEY = "DEMO_KEY"

# ======================================================================
# LOAD FOOD DATABASE FROM CSV
# ======================================================================
import csv

def load_food_database():
    """Load food data from south_indian_foods.csv"""
    data = {}
    suggestions = {'breakfast': [], 'lunch': [], 'dinner': [], 'snack': []}
    csv_path = os.path.join(BASE_DIR, 'south_indian_foods.csv')
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row['food_name'].strip()
                key = name.lower()
                data[key] = {
                    'calories': float(row['calories']),
                    'protein': float(row['protein']),
                    'fat': float(row['fat']),
                    'carbs': float(row['carbs']),
                    'fiber': float(row.get('fiber', 0) or 0),
                    'category': row.get('category', ''),
                }
                # Build suggestions from meal_type column
                meal_types = row.get('meal_type', '').split('|')
                for mt in meal_types:
                    mt = mt.strip().lower()
                    if mt in suggestions and name not in suggestions[mt]:
                        suggestions[mt].append(name)
    except Exception as e:
        print(f"[WARN] Could not load CSV: {e}. Using minimal fallback data.")
        data = {
            "idli": {"calories": 120, "protein": 3.5, "fat": 0.5, "carbs": 24, "fiber": 1.0, "category": "Breakfast"},
            "dosa": {"calories": 160, "protein": 3.0, "fat": 5.0, "carbs": 28, "fiber": 0.8, "category": "Breakfast"},
        }
        suggestions = {'breakfast': ['Idli', 'Dosa'], 'lunch': [], 'dinner': [], 'snack': []}
    return data, suggestions

SOUTH_INDIAN_DATA, SOUTH_INDIAN_SUGGESTIONS = load_food_database()
print(f"[OK] Loaded {len(SOUTH_INDIAN_DATA)} foods from CSV")

import requests

def get_nutrition_from_edamam(query):
    """Fetch nutrition from Edamam Food Database API"""
    if EDAMAM_APP_ID == "YOUR_APP_ID" or EDAMAM_APP_KEY == "YOUR_APP_KEY":
        return None

    # URL for food parser
    url = f"https://api.edamam.com/api/food-database/v2/parser?app_id={EDAMAM_APP_ID}&app_key={EDAMAM_APP_KEY}&ingr={query}&nutrition-type=logging"
    
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get('hints'):
                f = data['hints'][0]['food']
                nuts = f.get('nutrients', {})
                return {
                    "calories": round(nuts.get('ENERC_KCAL', 0), 1),
                    "protein": round(nuts.get('PROCNT', 0), 1),
                    "fat": round(nuts.get('FAT', 0), 1),
                    "carbs": round(nuts.get('CHOCDF', 0), 1),
                    "fiber": round(nuts.get('FIBTG', 0), 1),
                    "food_name": f.get('label', query).capitalize(),
                    "source": "Edamam API",
                    "success": True
                }
    except Exception as e:
        print(f"Edamam Error: {e}")
    return None

def get_nutrition_from_usda(query):
    """Fetch nutrition from USDA FoodData Central API"""
    url = f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={USDA_API_KEY}&query={urllib.parse.quote(query)}&pageSize=1&dataType=Survey%20(FNDDS)"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            if data.get('foods'):
                f = data['foods'][0]
                nuts = {n['nutrientName']: n['value'] for n in f.get('foodNutrients', [])}
                return {
                    "calories": round(nuts.get('Energy', nuts.get('Energy (kcal)', 0)), 1),
                    "protein": round(nuts.get('Protein', 0), 1),
                    "fat": round(nuts.get('Total lipid (fat)', 0), 1),
                    "carbs": round(nuts.get('Carbohydrate, by difference', 0), 1),
                    "fiber": round(nuts.get('Fiber, total dietary', 0), 1),
                    "food_name": f.get('description', query).capitalize(),
                    "source": "USDA API",
                    "success": True
                }
    except Exception as e:
        print(f"USDA Error: {e}")
    return None

def get_nutrition(food_query):
    """Core logic: Local DB (Primary for South Indian) -> USDA -> Edamam -> Estimate"""
    q_lower = food_query.lower().strip()
    
    # Extract base food for local DB check if it's like '2 idli'
    base_food = q_lower
    m = re.match(r'^[\d\.]+\s*(?:grams|g|pieces|piece|cups|cup|bowl|plate|glass|oz|ml|l|kg)?\s*(.+)$', q_lower)
    if m:
        base_food = m.group(1).strip()
    
    # 1. Check South Indian DB first
    # Try exact match or singular form (e.g., 'idlis' -> 'idli')
    if base_food in SOUTH_INDIAN_DATA or base_food.rstrip('s') in SOUTH_INDIAN_DATA:
        key = base_food if base_food in SOUTH_INDIAN_DATA else base_food.rstrip('s')
        data = SOUTH_INDIAN_DATA[key]
        qty_grams = parse_quantity(food_query)
        ratio = qty_grams / 100.0
        return {
            "calories": round(data['calories'] * ratio, 1),
            "protein": round(data['protein'] * ratio, 1),
            "fat": round(data['fat'] * ratio, 1),
            "carbs": round(data['carbs'] * ratio, 1),
            "fiber": round(data.get('fiber', 0) * ratio, 1),
            "food_name": food_query.capitalize(),
            "source": "Local DB",
            "success": True
        }

    # 2. Try USDA API
    nutrition = get_nutrition_from_usda(food_query)
    if nutrition:
        return nutrition

    # 3. Try Edamam if USDA fails
    nutrition = get_nutrition_from_edamam(food_query)
    if nutrition:
        return nutrition

    # 3. Last resort: Estimate if quantity exists
    has_qty = re.search(r'\d', q_lower) 
    if has_qty:
        grams = parse_quantity(food_query)
        return {
            "calories": round(grams * 1.5, 1),
            "protein": round(grams * 0.08, 1),
            "fat": round(grams * 0.05, 1),
            "carbs": round(grams * 0.2, 1),
            "fiber": 0,
            "food_name": food_query,
            "source": "Estimate",
            "success": True
        }
    
    return {
        "calories": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0,
        "food_name": food_query, "source": "None", "success": False
    }

UNIT_MAP = {
    "g":1,"gram":1,"grams":1,"kg":1000,"ml":1,"l":1000,
    "oz":28.35,"cup":240,"cups":240,"tbsp":15,"tsp":5,
    "piece":100,"pieces":100,"slice":35,"slices":35,
    "bowl":280,"plate":320,"glass":240,
    "small":80,"medium":120,"large":180,
}
ITEM_WEIGHT = {
    "egg":50,"banana":120,"apple":150,"orange":130,"mango":200,
    "roti":40,"chapati":40,"paratha":80,"dosa":120,"idli":60,
    "samosa":60,"cookie":15,"biscuit":10,
    "ven pongal": 250, "chicken rice": 350, "vada": 50, "upma": 200,
    "sambar rice": 300, "curd rice": 250
}

def parse_quantity(query):
    """Extract grams from input like '2 eggs', '100g chicken', '1 cup rice'."""
    q = query.lower().strip()
    m = re.match(r'^([\d\.]+)\s*([a-z]+)\s+(.+)$', q)
    if m:
        qty, unit, food = float(m.group(1)), m.group(2), m.group(3).strip()
        if unit in UNIT_MAP:
            return qty * UNIT_MAP[unit]
    m = re.match(r'^([\d\.]+)\s+(.+)$', q)
    if m:
        qty, food = float(m.group(1)), m.group(2).strip()
        base = food.rstrip('s')
        wt = ITEM_WEIGHT.get(food) or ITEM_WEIGHT.get(base)
        return qty * wt if wt else qty * 100
    return 100.0

# ======================================================================
#  EXERCISE MET VALUES
# ======================================================================
EXERCISE_MET = {
    "walking":3.5,"jogging":7.0,"running":9.8,"cycling":7.5,
    "swimming":8.0,"yoga":3.0,"gym":5.0,"dancing":5.5,
    "skipping":12.0,"sports":7.0,
}

# ======================================================================
#  LSTM MODEL
# ======================================================================
import torch, torch.nn as nn, joblib

class NutritionLSTM(nn.Module):
    def __init__(self, input_size, hidden_size=64, num_layers=2, dropout=0.3):
        super().__init__()
        self.lstm = nn.LSTM(input_size=input_size, hidden_size=hidden_size,
                            num_layers=num_layers, batch_first=True,
                            dropout=dropout if num_layers > 1 else 0,
                            bidirectional=True)
        self.fc = nn.Sequential(
            nn.Linear(hidden_size * 2, 32), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(32, 16), nn.ReLU(), nn.Dropout(dropout * 0.5),
            nn.Linear(16, 1))
    def forward(self, x):
        out,_=self.lstm(x); return self.fc(out[:,-1,:])

lstm_model=None; feature_scaler=None; target_scaler=None
FEATURE_COLS=['calorie_intake','tdee','caloric_surplus']

def load_lstm_model():
    global lstm_model, feature_scaler, target_scaler
    try:
        ckpt=torch.load(os.path.join(BASE_DIR,'models','lstm_model.pth'),map_location='cpu',weights_only=False)
        lstm_model=NutritionLSTM(input_size=ckpt['input_size'],hidden_size=ckpt['hidden_size'],num_layers=ckpt['num_layers'])
        lstm_model.load_state_dict(ckpt['model_state_dict']); lstm_model.eval()
        feature_scaler=joblib.load(os.path.join(BASE_DIR,'models','feature_scaler.pkl'))
        target_scaler=joblib.load(os.path.join(BASE_DIR,'models','target_scaler.pkl'))
        print("[OK] LSTM model loaded")
    except Exception as e:
        print(f"[WARN] LSTM not loaded: {e}")

# ======================================================================
#  DATABASE
# ======================================================================
class DBConnectionWrapper:
    def __init__(self):
        self.db_url = os.environ.get('DATABASE_URL')
        self.is_postgres = bool(self.db_url and self.db_url.startswith('postgres'))
        
        if self.is_postgres and psycopg2:
            self.conn = psycopg2.connect(self.db_url, cursor_factory=psycopg2.extras.DictCursor)
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row

    def execute(self, query, params=()):
        if self.is_postgres:
            pg_query = query.replace('?', '%s')
            cursor = self.conn.cursor()
            try:
                cursor.execute(pg_query, params)
            except Exception as e:
                self.conn.rollback()
                raise e
            return cursor
        else:
            return self.conn.execute(query, params)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def get_db():
    return DBConnectionWrapper()

def init_db():
    conn = get_db()
    if conn.is_postgres:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
            age INTEGER, gender TEXT, height REAL, weight REAL,
            activity TEXT DEFAULT 'moderate', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS food_logs (
            id SERIAL PRIMARY KEY, user_id INTEGER,
            food_name TEXT, meal_type TEXT, calories REAL, protein REAL,
            fat REAL, carbs REAL DEFAULT 0, log_date DATE, log_time TIME,
            FOREIGN KEY(user_id) REFERENCES users(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS water_logs (
            id SERIAL PRIMARY KEY, user_id INTEGER,
            glasses INTEGER DEFAULT 0, log_date DATE,
            FOREIGN KEY(user_id) REFERENCES users(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS weight_logs (
            id SERIAL PRIMARY KEY, user_id INTEGER,
            weight REAL, log_date DATE, FOREIGN KEY(user_id) REFERENCES users(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS exercise_logs (
            id SERIAL PRIMARY KEY, user_id INTEGER,
            exercise_type TEXT, duration_hours REAL, calories_burned REAL,
            log_date DATE, FOREIGN KEY(user_id) REFERENCES users(id))''')
    else:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
            age INTEGER, gender TEXT, height REAL, weight REAL,
            activity TEXT DEFAULT 'moderate', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS food_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            food_name TEXT, meal_type TEXT, calories REAL, protein REAL,
            fat REAL, carbs REAL DEFAULT 0, log_date DATE, log_time TIME,
            FOREIGN KEY(user_id) REFERENCES users(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS water_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            glasses INTEGER DEFAULT 0, log_date DATE,
            FOREIGN KEY(user_id) REFERENCES users(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            weight REAL, log_date DATE, FOREIGN KEY(user_id) REFERENCES users(id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS exercise_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            exercise_type TEXT, duration_hours REAL, calories_burned REAL,
            log_date DATE, FOREIGN KEY(user_id) REFERENCES users(id))''')
    conn.commit()
    conn.close()

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()
def get_user(uid):
    conn=get_db(); u=conn.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); conn.close(); return u

def calc_bmi(w,h): return round(w/(h/100)**2,1)
def bmi_status(bmi):
    if bmi<18.5: return 'Underweight','gain','#f5a623'
    elif bmi<25: return 'Normal','maintain','#a8e063'
    elif bmi<30: return 'Overweight','lose','#f5a623'
    else: return 'Obese','lose','#e05c5c'
def calc_bmr(w,h,age,gender):
    if gender.lower()=='male': return round(88.362+13.397*w+4.799*h-5.677*age,1)
    return round(447.593+9.247*w+3.098*h-4.330*age,1)
def calc_tdee(bmr,activity):
    m={'sedentary':1.2,'light':1.375,'moderate':1.55,'active':1.725,'very_active':1.9}
    return round(bmr*m.get(activity,1.55))
def cal_target(tdee,goal):
    if goal=='lose': return tdee-500
    elif goal=='gain': return tdee+300
    return tdee

# ======================================================================
#  LSTM PREDICTION (uses last 7 days of user food data)
# ======================================================================
def predict_with_lstm(uid, current_weight):
    user=get_user(uid)
    bmr=calc_bmr(user['weight'],user['height'],user['age'],user['gender'])
    tdee=calc_tdee(bmr,user['activity'] or 'moderate')
    bmi=calc_bmi(user['weight'],user['height'])
    _,goal,_=bmi_status(bmi)
    conn=get_db()

    last_15_data=[]
    for i in range(14,-1,-1):
        d=(date.today()-timedelta(days=i)).isoformat()
        food=conn.execute('SELECT SUM(calories) cal,SUM(protein) prot,SUM(fat) fat,SUM(carbs) carbs FROM food_logs WHERE user_id=? AND log_date=?',(uid,d)).fetchone()
        water=conn.execute('SELECT glasses FROM water_logs WHERE user_id=? AND log_date=?',(uid,d)).fetchone()
        exercise=conn.execute('SELECT SUM(calories_burned) burned FROM exercise_logs WHERE user_id=? AND log_date=?',(uid,d)).fetchone()
        day_cal=float(food['cal'] or 0)
        day_burned=float(exercise['burned'] or 0) if exercise and exercise['burned'] else 0
        last_15_data.append({
            'date':d,'calories':day_cal,'protein':float(food['prot'] or 0),
            'fat':float(food['fat'] or 0),'carbs':float(food['carbs'] or 0),
            'fiber':25.0,'calories_burned':day_burned,
            'exercise_burned':day_burned,'net_calories':round(day_cal-day_burned,1),
        })
    conn.close()

    # Calculate average daily calories burned
    total_burned = sum(d['exercise_burned'] for d in last_15_data)
    avg_burned = total_burned / 15 if len(last_15_data) > 0 else 0
    exercise_daily_loss = round(avg_burned / 7700, 4) # Weight loss from exercise alone in kg/day
    exercise_weekly_loss = round(exercise_daily_loss * 7, 2)

    # Count how many days actually have food data logged
    days_with_data = sum(1 for d in last_15_data if d['calories'] > 0)
    total_cal = sum(d['calories'] for d in last_15_data)
    today = date(2026, 3, 31)

    # Only use LSTM if model loaded AND at least 7 days have real food data
    if lstm_model is not None and days_with_data >= 7:
        features=np.array([[d['calories'],tdee,d['calories']-tdee] for d in last_15_data],dtype=np.float32)
        features_scaled=feature_scaler.transform(features)
        sequence=features_scaled.copy()

        # Calculate physics-based daily weight change from calorie surplus/deficit
        avg_net_cal = sum(d['net_calories'] for d in last_15_data if d['calories'] > 0) / days_with_data
        daily_surplus = avg_net_cal - tdee  # positive = surplus (gain), negative = deficit (lose)
        physics_daily_chg = daily_surplus / 7700  # 7700 kcal ≈ 1 kg body weight

        preds=[]; w=current_weight
        for i in range(1,16):
            inp=torch.from_numpy(sequence.reshape(1,15,len(FEATURE_COLS)))
            with torch.no_grad(): pred_scaled=lstm_model(inp).numpy()
            lstm_wc=float(target_scaler.inverse_transform(pred_scaled)[0][0])

            # Hybrid: blend LSTM (40%) + physics-based (60%)
            # This ensures calorie surplus/deficit drives direction correctly
            wc = 0.4 * lstm_wc + 0.6 * physics_daily_chg

            w=round(w+wc,2)
            preds.append({'date':(today+timedelta(days=i)).strftime('%b %d'),'weight':w,'change':round(wc,4)})
            sequence=np.vstack([sequence[1:],sequence[-1:]])
        tc=preds[-1]['weight']-current_weight
        if tc < -0.2:
            trend = "LOSS"
        elif tc > 0.2:
            trend = "GAIN"
        else:
            trend = "STABLE"
        # Build goal-aware message
        if goal == 'lose':
            if trend == "LOSS":
                msg = f"✅ LSTM predicts weight LOSS (-{abs(round(tc,2))} kg). You're on track for your weight loss goal!"
            elif trend == "GAIN":
                msg = f"⚠️ LSTM predicts weight GAIN (+{round(tc,2)} kg). You're eating above your TDEE ({tdee} kcal). Reduce intake to lose weight."
            else:
                msg = f"ℹ️ LSTM predicts STABLE weight. Eat below {tdee} kcal/day to start losing weight."
        elif goal == 'gain':
            if trend == "GAIN":
                msg = f"✅ LSTM predicts weight GAIN (+{round(tc,2)} kg). You're on track for your weight gain goal!"
            elif trend == "LOSS":
                msg = f"⚠️ LSTM predicts weight LOSS (-{abs(round(tc,2))} kg). You're eating below your TDEE ({tdee} kcal). Eat more to gain weight."
            else:
                msg = f"ℹ️ LSTM predicts STABLE weight. Eat above {tdee} kcal/day to start gaining weight."
        else:  # maintain
            if trend == "STABLE":
                msg = f"✅ LSTM predicts STABLE weight. You're maintaining well around your TDEE ({tdee} kcal)!"
            elif trend == "LOSS":
                msg = f"ℹ️ LSTM predicts weight LOSS (-{abs(round(tc,2))} kg). Eat closer to {tdee} kcal/day to maintain."
            else:
                msg = f"ℹ️ LSTM predicts weight GAIN (+{round(tc,2)} kg). Eat closer to {tdee} kcal/day to maintain."
    else:
        # Calorie-based prediction using actual intake vs TDEE
        if days_with_data > 0:
            avg_net_cal = sum(d['net_calories'] for d in last_15_data if d['calories'] > 0) / days_with_data
            daily_surplus = avg_net_cal - tdee
            daily_chg = daily_surplus / 7700
        else:
            # If no food data, show impact of exercise alone (assuming maintenance intake)
            daily_chg = -avg_burned / 7700

        preds=[]; w=current_weight
        for i in range(1,16):
            # Only add noise if there is some real data to avoid confusing Gain/Loss labels on empty accounts
            noise = float(np.random.uniform(-0.005, 0.005)) if days_with_data > 0 else 0.0
            chg = round(daily_chg + noise, 4)
            w = round(w + chg, 2)
            preds.append({'date':(today+timedelta(days=i)).strftime('%b %d'),'weight':w,'change':chg})

        if days_with_data == 0:
            msg = f"❌ No food data logged yet! Log at least 7 days of meals for accurate LSTM predictions. Your TDEE is {tdee} kcal/day."
        elif days_with_data < 7:
            direction = "GAIN" if daily_chg > 0.01 else ("LOSS" if daily_chg < -0.01 else "STABLE")
            msg = f"⚠️ Only {days_with_data}/15 days have food data (need 7+ for LSTM). Basic prediction shows weight {direction}. Log more days for better results!"
        else:
            # Has 3+ days but LSTM model not loaded
            direction = "GAIN" if daily_chg > 0.01 else ("LOSS" if daily_chg < -0.01 else "STABLE")
            if goal == 'lose':
                if direction == "LOSS":
                    msg = f"✅ Based on avg intake ({round(avg_net_cal)} kcal vs TDEE {tdee}), you're losing weight! Run train_model.py for LSTM predictions."
                else:
                    msg = f"⚠️ Avg intake ({round(avg_net_cal)} kcal) is {'above' if daily_chg>0 else 'at'} TDEE ({tdee}). Eat less to lose weight. Run train_model.py for LSTM."
            elif goal == 'gain':
                if direction == "GAIN":
                    msg = f"✅ Based on avg intake ({round(avg_net_cal)} kcal vs TDEE {tdee}), you're gaining weight! Run train_model.py for LSTM predictions."
                else:
                    msg = f"⚠️ Avg intake ({round(avg_net_cal)} kcal) is {'below' if daily_chg<0 else 'at'} TDEE ({tdee}). Eat more to gain weight. Run train_model.py for LSTM."
            else:
                msg = f"ℹ️ Avg intake ({round(avg_net_cal)} kcal) vs TDEE ({tdee}). Weight trend: {direction}. Run train_model.py for LSTM predictions."

    # Add specific exercise impact to the returned data
    exercise_stats = {
        'avg_daily_burned': round(avg_burned, 1),
        'daily_loss_kg': exercise_daily_loss,
        'weekly_loss_kg': exercise_weekly_loss
    }

    return preds, msg, last_15_data, tdee, goal, days_with_data, exercise_stats

# ======================================================================
#  ROUTES
# ======================================================================
@app.route('/')
def index(): return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method=='POST':
        d=request.form
        try:
            conn=get_db()
            conn.execute('INSERT INTO users (name,email,password,age,gender,height,weight,activity) VALUES (?,?,?,?,?,?,?,?)',
                (d['name'],d['email'],hash_pw(d['password']),int(d['age']),d['gender'],float(d['height']),float(d['weight']),d.get('activity','moderate')))
            conn.commit()
            uid=conn.execute('SELECT id FROM users WHERE email=?',(d['email'],)).fetchone()['id']
            conn.execute('INSERT INTO weight_logs (user_id,weight,log_date) VALUES (?,?,?)',(uid,float(d['weight']),date.today().isoformat()))
            conn.commit(); conn.close()
            return redirect(url_for('login'))
        except Exception as e:
            if 'IntegrityError' in type(e).__name__:
                conn.close(); return render_template('register.html',error='Email already registered.')
            raise
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        conn=get_db()
        u=conn.execute('SELECT * FROM users WHERE email=? AND password=?',(request.form['email'],hash_pw(request.form['password']))).fetchone()
        conn.close()
        if u: session['user_id']=u['id']; session['user_name']=u['name']; return redirect(url_for('dashboard'))
        return render_template('login.html',error='Invalid email or password.')
    return render_template('login.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; user=get_user(uid); today=date.today().isoformat()
    if user is None: session.clear(); return redirect(url_for('login'))
    bmi=calc_bmi(user['weight'],user['height'])
    status,goal,status_color=bmi_status(bmi)
    bmr=calc_bmr(user['weight'],user['height'],user['age'],user['gender'])
    tdee=calc_tdee(bmr,user['activity'] or 'moderate')
    ctarget=cal_target(tdee,goal); p_target=round(user['weight']*1.6,1); f_target=round(user['weight']*0.8,1)
    conn=get_db()
    logs=conn.execute('SELECT * FROM food_logs WHERE user_id=? AND log_date=? ORDER BY log_time DESC',(uid,today)).fetchall()
    total_cal=round(sum(r['calories'] for r in logs),1)
    total_prot=round(sum(r['protein'] for r in logs),1)
    total_fat=round(sum(r['fat'] for r in logs),1)
    seven=[]
    for i in range(6,-1,-1):
        d=(date.today()-timedelta(days=i)).isoformat()
        r=conn.execute('SELECT SUM(calories) c FROM food_logs WHERE user_id=? AND log_date=?',(uid,d)).fetchone()
        seven.append({'date':d,'cal':round(r['c'] or 0,1)})
    wr=conn.execute('SELECT glasses FROM water_logs WHERE user_id=? AND log_date=?',(uid,today)).fetchone()
    water=wr['glasses'] if wr else 0
    ex=conn.execute('SELECT SUM(calories_burned) b FROM exercise_logs WHERE user_id=? AND log_date=?',(uid,today)).fetchone()
    today_burned=round(ex['b'] or 0,1) if ex and ex['b'] else 0
    exercises_today=conn.execute('SELECT * FROM exercise_logs WHERE user_id=? AND log_date=? ORDER BY id DESC',(uid,today)).fetchall()
    conn.close()
    return render_template('dashboard.html',user=user,bmi=bmi,status=status,status_color=status_color,
        goal=goal,bmr=bmr,tdee=tdee,ctarget=ctarget,p_target=p_target,f_target=f_target,
        total_cal=total_cal,total_prot=total_prot,total_fat=total_fat,
        remaining=round(ctarget-total_cal,1),logs=logs,seven=seven,water=water,
        today_burned=today_burned,exercises_today=exercises_today,
        exercise_types=list(EXERCISE_MET.keys()),
        now_hour=datetime.now().hour,today_str=date.today().strftime('%A, %B %d %Y'))

@app.route('/get_nutrition')
def get_nutrition_route():
    food=request.args.get('food','').strip()
    if not food: return jsonify({'calories':0,'protein':0,'fat':0,'carbs':0,'success':False})
    return jsonify(get_nutrition(food))

@app.route('/log_food', methods=['POST'])
def log_food():
    if 'user_id' not in session: return jsonify({'success':False})
    uid=session['user_id']; data=request.get_json()
    food=data.get('food','').strip(); meal=data.get('meal','snack')
    # If frontend sent nutrition, use it; otherwise look up
    if data.get('calories') and float(data['calories'])>0:
        cal,prot,fat,carbs=float(data['calories']),float(data.get('protein',0)),float(data.get('fat',0)),float(data.get('carbs',0))
        src=data.get('source','Manual')
    else:
        n=get_nutrition(food)
        if not n.get('success'):
            return jsonify({'success':False, 'error':'Food not found'})
        cal,prot,fat,carbs=n['calories'],n['protein'],n['fat'],n['carbs']
        src=n['source']
    now=datetime.now()
    conn=get_db()
    conn.execute('INSERT INTO food_logs (user_id,food_name,meal_type,calories,protein,fat,carbs,log_date,log_time) VALUES (?,?,?,?,?,?,?,?,?)',
        (uid,food,meal,cal,prot,fat,carbs,now.date().isoformat(),now.strftime('%H:%M:%S')))
    conn.commit(); conn.close()
    return jsonify({'success':True,'calories':cal,'protein':prot,'fat':fat,'carbs':carbs,'source':src,'food':food,'meal':meal,'time':now.strftime('%H:%M')})

@app.route('/get_suggestions')
def get_suggestions():
    meal = request.args.get('meal','breakfast').lower()
    items = SOUTH_INDIAN_SUGGESTIONS.get(meal, SOUTH_INDIAN_SUGGESTIONS['breakfast'])
    return jsonify({'suggestions': items})

@app.route('/download_food_db')
def download_food_db():
    """Download the food nutrition database CSV"""
    from flask import send_file
    csv_path = os.path.join(BASE_DIR, 'south_indian_foods.csv')
    return send_file(csv_path, as_attachment=True, download_name='south_indian_foods.csv', mimetype='text/csv')

@app.route('/search_food')
def search_food():
    q = request.args.get('q', '').lower().strip()
    if not q: return jsonify([])
    
    # Always search local DB first
    local_matches = set()
    # Search SOUTH_INDIAN_DATA keys — prioritize items starting with query
    starts_with = []
    contains = []
    for item in SOUTH_INDIAN_DATA.keys():
        if item.startswith(q):
            starts_with.append(item.title())
        elif q in item:
            contains.append(item.title())
    # Also search SOUTH_INDIAN_SUGGESTIONS
    for meal, items in SOUTH_INDIAN_SUGGESTIONS.items():
        for item in items:
            if item.lower().startswith(q):
                if item not in starts_with:
                    starts_with.append(item)
            elif q in item.lower():
                if item not in contains:
                    contains.append(item)
    
    local_results = starts_with + contains
    # Remove duplicates while keeping order
    seen = set()
    local_results = [x for x in local_results if not (x.lower() in seen or seen.add(x.lower()))]
    
    # If we have 8+ local results, return them immediately
    if len(local_results) >= 8:
        return jsonify(local_results[:10])
    
    # Merge with Edamam API if available
    if EDAMAM_APP_ID != "YOUR_APP_ID":
        try:
            url = f"https://api.edamam.com/auto-complete?app_id={EDAMAM_APP_ID}&app_key={EDAMAM_APP_KEY}&q={q}&limit=8"
            res = requests.get(url, timeout=3)
            if res.status_code == 200:
                api_results = [s.capitalize() for s in res.json()]
                # Merge: local first, then API (no duplicates)
                local_lower = {x.lower() for x in local_results}
                for item in api_results:
                    if item.lower() not in local_lower:
                        local_results.append(item)
        except: pass
    
    return jsonify(local_results[:10])

@app.route('/log_exercise', methods=['POST'])
def log_exercise():
    if 'user_id' not in session: return jsonify({'success':False})
    uid=session['user_id']; data=request.get_json()
    ex_type=data.get('exercise_type','walking'); hours=float(data.get('hours',0.5))
    user=get_user(uid); met=EXERCISE_MET.get(ex_type,5.0)
    burned=round(met*user['weight']*hours,1)
    conn=get_db()
    conn.execute('INSERT INTO exercise_logs (user_id,exercise_type,duration_hours,calories_burned,log_date) VALUES (?,?,?,?,?)',
        (uid,ex_type,hours,burned,date.today().isoformat()))
    conn.commit(); conn.close()
    return jsonify({'success':True,'exercise_type':ex_type,'hours':hours,'burned':burned})

@app.route('/log_water', methods=['POST'])
def log_water():
    if 'user_id' not in session: return jsonify({'success':False})
    uid=session['user_id']; action=request.json.get('action','add'); today=date.today().isoformat()
    conn=get_db()
    row=conn.execute('SELECT glasses FROM water_logs WHERE user_id=? AND log_date=?',(uid,today)).fetchone()
    nv=max(0,(row['glasses'] if row else 0)+(1 if action=='add' else -1))
    if row: conn.execute('UPDATE water_logs SET glasses=? WHERE user_id=? AND log_date=?',(nv,uid,today))
    else: conn.execute('INSERT INTO water_logs (user_id,glasses,log_date) VALUES (?,?,?)',(uid,nv,today))
    conn.commit(); conn.close()
    return jsonify({'glasses':nv})

@app.route('/history')
def history():
    if 'user_id' not in session: return redirect(url_for('login'))
    conn=get_db()
    logs=conn.execute('SELECT * FROM food_logs WHERE user_id=? ORDER BY log_date DESC,log_time DESC LIMIT 200',(session['user_id'],)).fetchall()
    conn.close()
    return render_template('history.html',logs=logs)

@app.route('/activity')
def activity():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; user=get_user(uid); today=date.today().isoformat()
    if user is None: session.clear(); return redirect(url_for('login'))
    conn=get_db()
    wr=conn.execute('SELECT glasses FROM water_logs WHERE user_id=? AND log_date=?',(uid,today)).fetchone()
    water=wr['glasses'] if wr else 0
    exercises_today=conn.execute('SELECT * FROM exercise_logs WHERE user_id=? AND log_date=? ORDER BY id DESC',(uid,today)).fetchall()
    total_burned=round(sum(e['calories_burned'] for e in exercises_today),1)
    conn.close()
    return render_template('activity.html', user=user, water=water,
        exercises_today=exercises_today, today_burned=total_burned,
        exercise_types=list(EXERCISE_MET.keys()), today_str=date.today().strftime('%A, %B %d %Y'))

@app.route('/predict')
def predict():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; user=get_user(uid)
    if user is None: session.clear(); return redirect(url_for('login'))
    run=request.args.get('run','0')=='1'
    preds,msg,last_15,tdee,goal,days_with_data,ex_stats=predict_with_lstm(uid,user['weight'])
    return render_template('predict.html',user=user,preds=preds,msg=msg,last_15_data=last_15,
        model_loaded=lstm_model is not None,tdee=tdee,goal=goal,days_with_data=days_with_data,
        prediction_run=run, ex_stats=ex_stats)

@app.route('/profile', methods=['GET','POST'])
def profile():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']
    if get_user(uid) is None: session.clear(); return redirect(url_for('login'))
    if request.method=='POST':
        d=request.form; conn=get_db()
        conn.execute('UPDATE users SET weight=?,height=?,age=?,activity=? WHERE id=?',(float(d['weight']),float(d['height']),int(d['age']),d['activity'],uid))
        conn.execute('INSERT INTO weight_logs (user_id,weight,log_date) VALUES (?,?,?)',(uid,float(d['weight']),date.today().isoformat()))
        conn.commit(); conn.close()
        return redirect(url_for('dashboard'))
    return render_template('profile.html',user=get_user(uid))

# ======================================================================
#  MEAL PLAN DATA — Goal-based food plans
# ======================================================================
MEAL_PLANS = {
    'lose': {
        'name': 'Weight Loss Plan',
        'cal_split': {'breakfast': 0.25, 'lunch': 0.35, 'dinner': 0.30, 'snack': 0.10},
        'meals': {
            'breakfast': [
                {'food': 'Idli', 'qty': '3 pieces (180g)', 'cal': 216, 'protein': 6.3, 'fat': 0.9},
                {'food': 'Pesarattu', 'qty': '2 pieces (200g)', 'cal': 260, 'protein': 14.0, 'fat': 3.0},
                {'food': 'Boiled Egg', 'qty': '2 eggs (100g)', 'cal': 155, 'protein': 13.0, 'fat': 11.0},
                {'food': 'Omelette + Toast', 'qty': '1 egg + 1 slice', 'cal': 230, 'protein': 14.0, 'fat': 13.0},
                {'food': 'Upma', 'qty': '1 bowl (200g)', 'cal': 300, 'protein': 7.0, 'fat': 9.0},
            ],
            'lunch': [
                {'food': 'Sambar Rice', 'qty': '1 plate (300g)', 'cal': 405, 'protein': 12.0, 'fat': 9.0},
                {'food': 'Curd Rice', 'qty': '1 plate (250g)', 'cal': 325, 'protein': 8.8, 'fat': 7.5},
                {'food': 'Chapati + Dal', 'qty': '2 chapati + 1 bowl dal', 'cal': 360, 'protein': 16.0, 'fat': 7.5},
                {'food': 'Lemon Rice + Poriyal', 'qty': '1 plate + 1 bowl', 'cal': 380, 'protein': 8.0, 'fat': 12.0},
                {'food': 'Brown Rice + Rasam', 'qty': '1 plate (250g)', 'cal': 310, 'protein': 7.5, 'fat': 3.0},
            ],
            'dinner': [
                {'food': 'Dosa + Sambar', 'qty': '2 dosa + 1 bowl sambar', 'cal': 350, 'protein': 9.0, 'fat': 11.0},
                {'food': 'Chapati + Chicken Curry', 'qty': '2 chapati + 100g curry', 'cal': 390, 'protein': 22.0, 'fat': 11.0},
                {'food': 'Idli + Sambar', 'qty': '3 idli + 1 bowl sambar', 'cal': 280, 'protein': 10.0, 'fat': 2.0},
                {'food': 'Appam + Egg Curry', 'qty': '2 appam + 1 bowl', 'cal': 330, 'protein': 13.0, 'fat': 10.0},
                {'food': 'Roti + Palak Paneer', 'qty': '2 roti + 100g', 'cal': 410, 'protein': 17.0, 'fat': 15.0},
            ],
            'snack': [
                {'food': 'Sundal', 'qty': '1 bowl (100g)', 'cal': 120, 'protein': 6.0, 'fat': 2.0},
                {'food': 'Buttermilk', 'qty': '1 glass (200ml)', 'cal': 36, 'protein': 3.0, 'fat': 0.6},
                {'food': 'Banana', 'qty': '1 medium', 'cal': 108, 'protein': 1.3, 'fat': 0.4},
                {'food': 'Filter Coffee', 'qty': '1 cup (150ml)', 'cal': 68, 'protein': 2.3, 'fat': 2.3},
                {'food': 'Tender Coconut', 'qty': '1 glass (200ml)', 'cal': 40, 'protein': 1.4, 'fat': 0.4},
            ]
        },
        'tips': [
            'Eat slowly — take 20 minutes per meal',
            'Drink warm water before each meal',
            'Avoid rice at dinner — choose chapati or dosa',
            'Avoid sweets and fried snacks',
            'Walk 30 minutes after dinner'
        ]
    },
    'gain': {
        'name': 'Weight Gain Plan',
        'cal_split': {'breakfast': 0.25, 'lunch': 0.30, 'dinner': 0.30, 'snack': 0.15},
        'meals': {
            'breakfast': [
                {'food': 'Masala Dosa + Chutney', 'qty': '2 dosa + chutney', 'cal': 480, 'protein': 9.5, 'fat': 19.0},
                {'food': 'Poori + Aloo', 'qty': '4 poori + potato curry', 'cal': 550, 'protein': 12.0, 'fat': 22.0},
                {'food': 'Ven Pongal + Vada', 'qty': '1 bowl + 2 vada', 'cal': 530, 'protein': 13.0, 'fat': 22.0},
                {'food': 'Paratha + Curd', 'qty': '2 paratha + 100g curd', 'cal': 520, 'protein': 12.5, 'fat': 21.0},
                {'food': 'Puttu + Banana', 'qty': '2 puttu + 2 banana', 'cal': 640, 'protein': 8.0, 'fat': 9.0},
            ],
            'lunch': [
                {'food': 'Chicken Biryani', 'qty': '1 big plate (400g)', 'cal': 880, 'protein': 42.0, 'fat': 32.0},
                {'food': 'Full Meals + Fish Curry', 'qty': '1 plate rice + sides', 'cal': 750, 'protein': 28.0, 'fat': 18.0},
                {'food': 'Mutton Biryani', 'qty': '1 big plate (400g)', 'cal': 960, 'protein': 48.0, 'fat': 40.0},
                {'food': 'Ghee Rice + Chicken Curry', 'qty': '1 plate + 150g', 'cal': 680, 'protein': 27.0, 'fat': 19.0},
                {'food': 'Sambar Rice + Vada + Papad', 'qty': '1 plate full', 'cal': 620, 'protein': 16.0, 'fat': 15.0},
            ],
            'dinner': [
                {'food': 'Parotta + Chicken Curry', 'qty': '3 parotta + 150g curry', 'cal': 720, 'protein': 28.0, 'fat': 30.0},
                {'food': 'Kothu Parotta', 'qty': '1 big plate (350g)', 'cal': 700, 'protein': 20.0, 'fat': 28.0},
                {'food': 'Fried Rice + Chicken 65', 'qty': '1 plate + 150g', 'cal': 650, 'protein': 30.0, 'fat': 25.0},
                {'food': 'Egg Biryani + Raita', 'qty': '1 plate (350g)', 'cal': 600, 'protein': 22.0, 'fat': 18.0},
                {'food': 'Naan + Paneer Butter Masala', 'qty': '2 naan + 150g', 'cal': 680, 'protein': 24.0, 'fat': 32.0},
            ],
            'snack': [
                {'food': 'Banana + Milk', 'qty': '2 banana + 1 glass milk', 'cal': 340, 'protein': 8.0, 'fat': 7.0},
                {'food': 'Lassi + Samosa', 'qty': '1 glass + 2 samosa', 'cal': 450, 'protein': 10.0, 'fat': 20.0},
                {'food': 'Vada + Coffee', 'qty': '3 vada + 1 coffee', 'cal': 380, 'protein': 14.0, 'fat': 18.0},
                {'food': 'Murukku + Tea', 'qty': '50g murukku + tea', 'cal': 260, 'protein': 4.5, 'fat': 11.0},
                {'food': 'Halwa + Milk', 'qty': '100g halwa + 1 glass', 'cal': 420, 'protein': 7.0, 'fat': 16.0},
            ]
        },
        'tips': [
            'Eat 5-6 smaller meals instead of 3 big ones',
            'Add ghee or butter to rice and chapati',
            'Drink banana milkshake or lassi daily',
            'Include protein in every meal — eggs, chicken, paneer',
            'Have a heavy dinner — parotta or biryani'
        ]
    },
    'maintain': {
        'name': 'Weight Maintenance Plan',
        'cal_split': {'breakfast': 0.25, 'lunch': 0.35, 'dinner': 0.30, 'snack': 0.10},
        'meals': {
            'breakfast': [
                {'food': 'Dosa + Chutney', 'qty': '2 dosa + chutney', 'cal': 350, 'protein': 7.0, 'fat': 12.0},
                {'food': 'Idli + Sambar', 'qty': '3 idli + sambar', 'cal': 280, 'protein': 10.0, 'fat': 2.5},
                {'food': 'Uttapam + Chutney', 'qty': '2 uttapam', 'cal': 380, 'protein': 9.0, 'fat': 11.0},
                {'food': 'Poha + Tea', 'qty': '1 bowl + 1 cup tea', 'cal': 310, 'protein': 5.5, 'fat': 8.0},
                {'food': 'Omelette + Toast', 'qty': '2 egg + 2 toast', 'cal': 380, 'protein': 20.0, 'fat': 18.0},
            ],
            'lunch': [
                {'food': 'Sambar Rice + Poriyal', 'qty': '1 plate + sides', 'cal': 450, 'protein': 12.0, 'fat': 10.0},
                {'food': 'Veg Biryani + Raita', 'qty': '1 plate', 'cal': 480, 'protein': 12.0, 'fat': 14.0},
                {'food': 'Chapati + Dal + Curd', 'qty': '2 chapati + dal + curd', 'cal': 420, 'protein': 16.0, 'fat': 10.0},
                {'food': 'Lemon Rice + Chicken Curry', 'qty': '1 plate + 100g curry', 'cal': 510, 'protein': 20.0, 'fat': 13.0},
                {'food': 'Curd Rice + Papad', 'qty': '1 plate + 1 papad', 'cal': 380, 'protein': 10.0, 'fat': 8.0},
            ],
            'dinner': [
                {'food': 'Rava Dosa + Sambar', 'qty': '2 dosa + sambar', 'cal': 370, 'protein': 8.5, 'fat': 12.0},
                {'food': 'Chapati + Egg Curry', 'qty': '2 chapati + egg curry', 'cal': 400, 'protein': 16.0, 'fat': 14.0},
                {'food': 'Appam + Chicken Curry', 'qty': '2 appam + 100g curry', 'cal': 420, 'protein': 18.0, 'fat': 10.0},
                {'food': 'Idiyappam + Egg Curry', 'qty': '3 idiyappam + curry', 'cal': 390, 'protein': 15.0, 'fat': 12.0},
                {'food': 'Dosa + Chutney + Sambar', 'qty': '2 dosa + sides', 'cal': 380, 'protein': 9.0, 'fat': 11.0},
            ],
            'snack': [
                {'food': 'Filter Coffee + Sundal', 'qty': '1 cup + 1 bowl', 'cal': 165, 'protein': 7.5, 'fat': 3.5},
                {'food': 'Fruit Bowl', 'qty': '1 bowl mixed fruits', 'cal': 120, 'protein': 2.0, 'fat': 0.5},
                {'food': 'Buttermilk + Banana', 'qty': '1 glass + 1 banana', 'cal': 126, 'protein': 2.6, 'fat': 0.7},
                {'food': 'Tea + Paniyaram', 'qty': '1 cup + 3 pieces', 'cal': 175, 'protein': 3.8, 'fat': 5.0},
                {'food': 'Tender Coconut', 'qty': '1 glass', 'cal': 40, 'protein': 1.4, 'fat': 0.4},
            ]
        },
        'tips': [
            'Keep portions consistent every day',
            'Balance carbs, protein, and fat in every meal',
            'Stay hydrated — 8 glasses of water daily',
            'Avoid skipping meals',
            'Exercise 30 mins daily to maintain weight'
        ]
    }
}

import random

@app.route('/meal_plan')
def meal_plan():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid=session['user_id']; user=get_user(uid)
    if user is None: session.clear(); return redirect(url_for('login'))
    bmi=calc_bmi(user['weight'],user['height'])
    status,goal,status_color=bmi_status(bmi)
    bmr=calc_bmr(user['weight'],user['height'],user['age'],user['gender'])
    tdee=calc_tdee(bmr,user['activity'] or 'moderate')
    target_cal=cal_target(tdee,goal)
    
    plan_data = MEAL_PLANS.get(goal, MEAL_PLANS['maintain'])
    
    # Pick one random option per meal
    import random
    today_plan = {}
    total_plan_cal = 0
    total_plan_prot = 0
    total_plan_fat = 0
    for meal_type in ['breakfast', 'lunch', 'dinner', 'snack']:
        options = plan_data['meals'][meal_type]
        # Use date-based seed so same plan appears all day
        seed = hash(date.today().isoformat() + meal_type + str(uid))
        rng = random.Random(seed)
        choice = rng.choice(options)
        budget = round(target_cal * plan_data['cal_split'][meal_type])
        today_plan[meal_type] = {
            'food': choice['food'],
            'qty': choice['qty'],
            'cal': choice['cal'],
            'protein': choice['protein'],
            'fat': choice['fat'],
            'budget': budget,
            'all_options': options
        }
        total_plan_cal += choice['cal']
        total_plan_prot += choice['protein']
        total_plan_fat += choice['fat']
    
    # Calculate weekly projection
    if goal == 'lose':
        daily_deficit = tdee - total_plan_cal
        weekly_change = round(daily_deficit * 7 / 7700, 2)  # kg
        projection_msg = f"Following this plan, you could lose ~{weekly_change} kg per week"
    elif goal == 'gain':
        daily_surplus = total_plan_cal - tdee
        weekly_change = round(daily_surplus * 7 / 7700, 2)
        projection_msg = f"Following this plan, you could gain ~{weekly_change} kg per week"
    else:
        weekly_change = 0
        projection_msg = "This plan maintains your current weight"
    
    return render_template('meal_plan.html',
        user=user, bmi=bmi, status=status, goal=goal, status_color=status_color,
        tdee=tdee, target_cal=target_cal, plan_name=plan_data['name'],
        today_plan=today_plan, total_cal=total_plan_cal,
        total_prot=round(total_plan_prot,1), total_fat=round(total_plan_fat,1),
        tips=plan_data['tips'], weekly_change=weekly_change,
        projection_msg=projection_msg, cal_split=plan_data['cal_split'])

if __name__=='__main__':
    init_db(); load_lstm_model(); app.run(debug=True,port=5000)
