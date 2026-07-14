import sqlite3
import hashlib
from datetime import date, timedelta
import random

def hash_pw(p): return hashlib.sha256(p.encode()).hexdigest()

DB_PATH = 'nutriai.db'

def populate():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    users_to_check = ['loss@test.com', 'gain@test.com', 'maintain@test.com', 'variance@test.com']
    for email in users_to_check:
        c.execute('DELETE FROM users WHERE email=?', (email,))
    
    # 1. Loss
    c.execute('''INSERT INTO users (name,email,password,age,gender,height,weight,activity)
                 VALUES (?,?,?,?,?,?,?,?)''', ('Weight Loss User', 'loss@test.com', hash_pw('123'), 30, 'Male', 170.0, 95.0, 'sedentary'))
    uid_loss = c.execute('SELECT id FROM users WHERE email=?', ('loss@test.com',)).fetchone()[0]

    # 2. Gain
    c.execute('''INSERT INTO users (name,email,password,age,gender,height,weight,activity)
                 VALUES (?,?,?,?,?,?,?,?)''', ('Weight Gain User', 'gain@test.com', hash_pw('123'), 25, 'Male', 180.0, 55.0, 'very_active'))
    uid_gain = c.execute('SELECT id FROM users WHERE email=?', ('gain@test.com',)).fetchone()[0]

    # 3. Maintain (Female, 160cm, 60kg, 28y, moderate. TDEE = 2134)
    c.execute('''INSERT INTO users (name,email,password,age,gender,height,weight,activity)
                 VALUES (?,?,?,?,?,?,?,?)''', ('Maintenance User', 'maintain@test.com', hash_pw('123'), 28, 'Female', 160.0, 60.0, 'moderate'))
    uid_main = c.execute('SELECT id FROM users WHERE email=?', ('maintain@test.com',)).fetchone()[0]

    # 4. Variance (Male, 175cm, 75kg, 25y, active)
    c.execute('''INSERT INTO users (name,email,password,age,gender,height,weight,activity)
                 VALUES (?,?,?,?,?,?,?,?)''', ('Erratic User', 'variance@test.com', hash_pw('123'), 25, 'Male', 175.0, 75.0, 'active'))
    uid_var = c.execute('SELECT id FROM users WHERE email=?', ('variance@test.com',)).fetchone()[0]

    # Delete any lingering logs just to be safe
    for uid in [uid_loss, uid_gain, uid_main, uid_var]:
        c.execute('DELETE FROM food_logs WHERE user_id=?', (uid,))
        c.execute('DELETE FROM exercise_logs WHERE user_id=?', (uid,))
        c.execute('DELETE FROM weight_logs WHERE user_id=?', (uid,))

    # Variance daily cal plan (extreme jumps)
    var_cals = [5000, 1200, 0, 2200, 3500, 1500, 4000, 3000, 1000, 2500, 4500, 0, 2000, 3200, 1500]

    for i in range(15):
        d = (date.today() - timedelta(days=14-i)).isoformat()
        t = "12:00:00"
        
        # Loss
        c.execute('INSERT INTO food_logs (user_id,food_name,meal_type,calories,protein,fat,carbs,log_date,log_time) VALUES (?,?,?,?,?,?,?,?,?)',
                  (uid_loss, 'Diet Food', 'lunch', 1400, 100, 30, 100, d, t))
        c.execute('INSERT INTO exercise_logs (user_id,exercise_type,duration_hours,calories_burned,log_date) VALUES (?,?,?,?,?)',
                  (uid_loss, 'running', 1.0, 500, d))

        # Gain
        c.execute('INSERT INTO food_logs (user_id,food_name,meal_type,calories,protein,fat,carbs,log_date,log_time) VALUES (?,?,?,?,?,?,?,?,?)',
                  (uid_gain, 'Heavy Food', 'lunch', 3500, 150, 100, 400, d, t))
        
        # Maintain (Needs accurately 2334 kcal because of 200 burn -> net 2134 = TDEE)
        c.execute('INSERT INTO food_logs (user_id,food_name,meal_type,calories,protein,fat,carbs,log_date,log_time) VALUES (?,?,?,?,?,?,?,?,?)',
                  (uid_main, 'Balanced Food', 'lunch', 2334, 100, 60, 250, d, t))
        c.execute('INSERT INTO exercise_logs (user_id,exercise_type,duration_hours,calories_burned,log_date) VALUES (?,?,?,?,?)',
                  (uid_main, 'walking', 0.5, 200, d))
                  
        # Variance
        vc = var_cals[i]
        if vc > 0:
            c.execute('INSERT INTO food_logs (user_id,food_name,meal_type,calories,protein,fat,carbs,log_date,log_time) VALUES (?,?,?,?,?,?,?,?,?)',
                      (uid_var, 'Mixed Food', 'random', vc, 80, 50, 200, d, t))
        # Random variance exercise
        ex = random.choice([0, 300, 600])
        if ex > 0:
            c.execute('INSERT INTO exercise_logs (user_id,exercise_type,duration_hours,calories_burned,log_date) VALUES (?,?,?,?,?)',
                      (uid_var, 'gym', 1.0, ex, d))

    conn.commit()
    conn.close()
    print("Users repopulated successfully.")
    print("Passwords for ALL test users are strictly '123'")

if __name__ == '__main__':
    populate()
