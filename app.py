\
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()
db = SQLAlchemy()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///kindness.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['ADMIN_PIN'] = os.getenv('ADMIN_PIN', '1234')
    db.init_app(app)

    # ------------------ MODELS ------------------
    class ClassSection(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        class_name = db.Column(db.String(10), nullable=False)
        section_name = db.Column(db.String(10), nullable=False)
        __table_args__ = (db.UniqueConstraint('class_name','section_name', name='uix_class_section'),)
        def label(self):
            return f"{self.class_name}-{self.section_name}"

    class Student(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(120), nullable=False)
        class_section_id = db.Column(db.Integer, db.ForeignKey('class_section.id'), nullable=False)
        class_section = db.relationship(ClassSection)

    class Teacher(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(120), nullable=False)
        subject = db.Column(db.String(120), nullable=False)

    class Staff(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(120), nullable=False)
        role = db.Column(db.String(120), nullable=False)

    class Note(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        giver_type = db.Column(db.String(20), nullable=False)  # 'student'|'teacher'
        giver_name = db.Column(db.String(120))                 # optional / Anonymous (but required if teacher)
        giver_section_id = db.Column(db.Integer, db.ForeignKey('class_section.id'))
        giver_section = db.relationship(ClassSection, foreign_keys=[giver_section_id])

        # Receiver generic
        receiver_type = db.Column(db.String(20), nullable=False)  # 'student'|'teacher'|'staff'
        message = db.Column(db.Text, nullable=False)

        # For Student receiver
        receiver_name_text = db.Column(db.String(120))            # free text student name
        receiver_section_id = db.Column(db.Integer, db.ForeignKey('class_section.id'))
        receiver_section = db.relationship(ClassSection, foreign_keys=[receiver_section_id])
        receiver_student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
        receiver_student = db.relationship(Student)

        # For Teacher receiver
        receiver_teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'))
        receiver_teacher = db.relationship(Teacher)

        # For Staff receiver
        receiver_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'))
        receiver_staff = db.relationship(Staff)

        # Meta
        weight_applied = db.Column(db.Integer, default=1)         # +1 same-sec, +2 cross-sec (student->student only)
        status = db.Column(db.String(20), default='pending')      # 'pending'|'approved'|'hidden'
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

    class Winner(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        period_type = db.Column(db.String(20))  # 'weekly'|'monthly'
        category = db.Column(db.String(40))     # 'top_student_manual'|'top_teacher_manual' etc.
        period_label = db.Column(db.String(40))
        title = db.Column(db.String(200))
        description = db.Column(db.Text)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

    class User(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        role = db.Column(db.String(20), nullable=False)  # 'student'|'teacher'|'staff'
        name = db.Column(db.String(120), nullable=False)
        class_section_id = db.Column(db.Integer, db.ForeignKey('class_section.id'))
        class_section = db.relationship(ClassSection)
        username = db.Column(db.String(120), unique=True, nullable=False)
        password_hash = db.Column(db.String(255), nullable=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

        def set_password(self, raw):
            self.password_hash = generate_password_hash(raw)

        def check_password(self, raw):
            return check_password_hash(self.password_hash, raw)

    with app.app_context():
        db.create_all()
        # --- lightweight auto-migrate for NOTE columns (if DB existed before) ---
        conn = db.engine.connect()
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(note)").fetchall()]
        def add_col(name, ddl):
            if name not in cols:
                conn.exec_driver_sql(f"ALTER TABLE note ADD COLUMN {name} {ddl};")
        for col, ddl in [
            ("receiver_type", "TEXT"),
            ("receiver_name_text", "TEXT"),
            ("receiver_section_id", "INTEGER"),
            ("receiver_teacher_id", "INTEGER"),
            ("receiver_staff_id", "INTEGER"),
        ]:
            add_col(col, ddl)

        # --- Robust seed defaults (idempotent) ---
        default_sections = [("6","A"),("7","A"),("7","B"),("8","A"),("8","B")]
        for c, s in default_sections:
            exists = ClassSection.query.filter_by(class_name=c, section_name=s).first()
            if not exists:
                db.session.add(ClassSection(class_name=c, section_name=s))
        db.session.commit()

        default_teachers = [("Mr. Rahman","Mathematics"),("Ms. Ayesha","English"),("Mr. Kabir","Science"),("Mrs. Sultana","Bangla")]
        for name, subj in default_teachers:
            if not Teacher.query.filter_by(name=name, subject=subj).first():
                db.session.add(Teacher(name=name, subject=subj))
        db.session.commit()

        default_staff = [("Anwar","Security"),("Rina","Cleaning"),("Masud","Office Assistant")]
        for name, role in default_staff:
            if not Staff.query.filter_by(name=name, role=role).first():
                db.session.add(Staff(name=name, role=role))
        db.session.commit()

    # ------------------ AUTH ------------------
    def admin_required():
        return session.get('admin_ok') is True

    def current_user():
        uid = session.get('uid')
        if not uid:
            return None
        return db.session.get(User, uid)

    @app.context_processor
    def inject_user():
        return { 'user': current_user() }

    @app.before_request
    def require_unlock():
        # allow static and auth/lock endpoints
        allowed = {'auth_login','auth_register','auth_logout','lock'}
        if request.endpoint in allowed or (request.path or '').startswith('/static'):
            return None
        # if logged in or unlocked in session, allow
        if current_user() or session.get('unlocked') is True:
            return None
        # otherwise send to lock screen
        return redirect(url_for('lock'))

    # ------------------ HELPERS ------------------
    def period_ranges():
        now = datetime.utcnow()
        weekly_start = now - timedelta(days=7)
        monthly_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        yearly_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return {
            "weekly": (weekly_start, now),
            "monthly": (monthly_start, now),
            "yearly": (yearly_start, now),
            "alltime": (datetime(1970,1,1), now)
        }

    def compute_leaders():
        prs = period_ranges()
        out = {}
        for key,(start,end) in prs.items():
            q = Note.query.filter(Note.status=='approved', Note.created_at >= start, Note.created_at <= end).all()
            app_sec = {}   
            appr_sec = {}  
            top_t = {}     

            for n in q:
                if n.receiver_type == 'student' and n.receiver_section_id:
                    app_sec[n.receiver_section_id] = app_sec.get(n.receiver_section_id, 0) + 1
                if n.giver_type == 'student' and n.giver_section_id:
                    appr_sec[n.giver_section_id] = appr_sec.get(n.giver_section_id, 0) + (n.weight_applied or 1)
                if n.receiver_type == 'teacher' and n.receiver_teacher_id:
                    top_t[n.receiver_teacher_id] = top_t.get(n.receiver_teacher_id, 0) + 1

            best_app_sec = max(app_sec.items(), key=lambda x: x[1]) if app_sec else None
            best_appr_sec = max(appr_sec.items(), key=lambda x: x[1]) if appr_sec else None
            best_teacher = max(top_t.items(), key=lambda x: x[1]) if top_t else None

            def sec_label(sec_id):
                sec = db.session.get(ClassSection, sec_id)
                return sec.label() if sec else "Unknown"

            def teacher_label(tid):
                t = db.session.get(Teacher, tid)
                return f"{t.name} — {t.subject}" if t else "Unknown"

            out[key] = {
                "top_appreciated_section": f"{sec_label(best_app_sec[0])} — {best_app_sec[1]}" if best_app_sec else None,
                "top_appreciator_section": f"{sec_label(best_appr_sec[0])} — {best_appr_sec[1]}" if best_appr_sec else None,
                "top_teacher": f"{teacher_label(best_teacher[0])} — {best_teacher[1]}" if best_teacher else None,
            }
        return out

    # ------------------ ROUTES ------------------
    @app.route('/')
    def home():
        counts = {
            "pending": Note.query.filter_by(status='pending').count(),
            "approved": Note.query.filter_by(status='approved').count(),
            "hidden": Note.query.filter_by(status='hidden').count(),
        }
        recent = Note.query.filter_by(status='approved').order_by(Note.created_at.desc()).limit(5).all()
        leader = compute_leaders()
        school = {"name": "Green Valley High School", "motto": "Kindness Every Day", "session": "2025"}
        return render_template('home.html', counts=counts, recent=recent, leader=leader, school=school)

    # ---- Post: Student ----
    @app.route('/post/student', methods=['GET','POST'])
    def post_student():
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()
        user = current_user()
        if request.method == 'POST':
            giver_type = request.form.get('giver_type','student').strip()
            giver_name = (request.form.get('giver_name') or '').strip()
            giver_section_id = request.form.get('giver_section_id') or None

            receiver_name = (request.form.get('receiver_name') or '').strip()
            receiver_section_id = request.form.get('receiver_section_id')
            message = (request.form.get('message') or '').strip()

            if giver_type == 'teacher' and not giver_name:
                flash('Teacher name is required.', 'error')
                return redirect(url_for('post_student'))
            if giver_type == 'student' and not giver_section_id:
                flash('Please select your own Class–Section.', 'error')
                return redirect(url_for('post_student'))
            if not receiver_name or not receiver_section_id:
                flash('Please enter the student name and select their Class–Section.', 'error')
                return redirect(url_for('post_student'))
            if not message:
                flash('Please write a short appreciation message.', 'error')
                return redirect(url_for('post_student'))

            if giver_type == 'student' and not giver_name:
                giver_name = 'Anonymous'

            weight = 1
            if giver_type == 'student' and giver_section_id:
                if str(giver_section_id) != str(receiver_section_id):
                    weight = 2

            note = Note(
                giver_type=giver_type,
                giver_name=giver_name,
                giver_section_id=int(giver_section_id) if giver_section_id else None,
                receiver_type='student',
                receiver_name_text=receiver_name,
                receiver_section_id=int(receiver_section_id),
                message=message,
                weight_applied=weight,
                status='pending'
            )
            db.session.add(note)
            db.session.commit()
            flash('Thanks! Your appreciation is pending admin approval.', 'ok')
            return redirect(url_for('wall'))
        return render_template('post_student.html', sections=sections, user=user)

    # ---- Post: Teacher ----
    @app.route('/post/teacher', methods=['GET','POST'])
    def post_teacher():
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()
        teachers = Teacher.query.order_by(Teacher.name).all()
        user = current_user()
        if request.method == 'POST':
            giver_type = request.form.get('giver_type','student').strip()
            giver_name = (request.form.get('giver_name') or '').strip()
            giver_section_id = request.form.get('giver_section_id') or None

            teacher_id = request.form.get('teacher_id')
            message = (request.form.get('message') or '').strip()

            if giver_type == 'teacher' and not giver_name:
                flash('Teacher name is required.', 'error')
                return redirect(url_for('post_teacher'))
            if giver_type == 'student' and not giver_section_id:
                flash('Please select your Class–Section.', 'error')
                return redirect(url_for('post_teacher'))
            if not teacher_id or not message:
                flash('Please select a teacher and write a message.', 'error')
                return redirect(url_for('post_teacher'))

            if giver_type == 'student' and not giver_name:
                giver_name = 'Anonymous'

            note = Note(
                giver_type=giver_type,
                giver_name=giver_name,
                giver_section_id=int(giver_section_id) if giver_section_id else None,
                receiver_type='teacher',
                receiver_teacher_id=int(teacher_id),
                message=message,
                weight_applied=1,
                status='pending'
            )
            db.session.add(note)
            db.session.commit()
            flash('Thanks! Your appreciation is pending admin approval.', 'ok')
            return redirect(url_for('wall'))
        return render_template('post_teacher.html', sections=sections, teachers=teachers, user=user)

    # ---- Post: Staff ----
    @app.route('/post/staff', methods=['GET','POST'])
    def post_staff():
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()
        staffers = Staff.query.order_by(Staff.name).all()
        user = current_user()
        if request.method == 'POST':
            giver_type = request.form.get('giver_type','student').strip()
            giver_name = (request.form.get('giver_name') or '').strip()
            giver_section_id = request.form.get('giver_section_id') or None

            staff_id = request.form.get('staff_id')
            message = (request.form.get('message') or '').strip()

            if giver_type == 'teacher' and not giver_name:
                flash('Teacher name is required.', 'error')
                return redirect(url_for('post_staff'))
            if giver_type == 'student' and not giver_section_id:
                flash('Please select your Class–Section.', 'error')
                return redirect(url_for('post_staff'))
            if not staff_id or not message:
                flash('Please select a staff member and write a message.', 'error')
                return redirect(url_for('post_staff'))

            if giver_type == 'student' and not giver_name:
                giver_name = 'Anonymous'

            note = Note(
                giver_type=giver_type,
                giver_name=giver_name,
                giver_section_id=int(giver_section_id) if giver_section_id else None,
                receiver_type='staff',
                receiver_staff_id=int(staff_id),
                message=message,
                weight_applied=1,
                status='pending'
            )                   
            db.session.add(note)
            db.session.commit()
            flash('Thanks! Your appreciation is pending admin approval.', 'ok')
            return redirect(url_for('wall'))
        return render_template('post_staff.html', sections=sections, staffers=staffers, user=user)

    # ---- Wall ----
    @app.route('/wall')
    def wall():
        notes = Note.query.filter_by(status='approved').order_by(Note.created_at.desc()).limit(200).all()

        # --- Aggregations for bar charts (top 4 + Others) ---
        from collections import defaultdict
        teacher_counts = defaultdict(int)
        staff_counts = defaultdict(int)
        student_counts = defaultdict(int)

        for n in notes:
            if n.receiver_type == 'teacher' and n.receiver_teacher:
                teacher_counts[n.receiver_teacher.name] += 1
            elif n.receiver_type == 'staff' and n.receiver_staff:
                staff_counts[n.receiver_staff.name] += 1
            elif n.receiver_type == 'student':
                label = None
                if n.receiver_student:
                    label = n.receiver_student.name
                elif n.receiver_name_text:
                    label = n.receiver_name_text
                if label:
                    student_counts[label] += 1

        def top_four_plus_others(counts_map):
            total = sum(counts_map.values()) or 1
            top = sorted(counts_map.items(), key=lambda x: x[1], reverse=True)[:4]
            items = []
            acc_pct = 0.0
            for name, cnt in top:
                pct = round(cnt * 100.0 / total, 1)
                acc_pct += pct
                items.append({"label": name, "pct": pct})
            others_pct = round(max(0.0, 100.0 - acc_pct), 1)
            if others_pct > 0 and len(counts_map) > len(top):
                items.append({"label": "Others", "pct": others_pct})
            return items

        bars = {
            "teachers": top_four_plus_others(teacher_counts),
            "students": top_four_plus_others(student_counts),
            "staffs": top_four_plus_others(staff_counts),
        }

        return render_template('wall.html', notes=notes, bars=bars)

    # ---- Leaders ----
    @app.route('/leaders')
    def leaders():
        approved = Note.query.filter_by(status='approved').all()
        appreciator_by_section = {}
        for n in approved:
            if n.giver_type == 'student' and n.giver_section_id:
                appreciator_by_section[n.giver_section_id] = appreciator_by_section.get(n.giver_section_id, 0) + (n.weight_applied or 1)
        appreciated_by_section = {}
        for n in approved:
            if n.receiver_type == 'student' and n.receiver_section_id:
                appreciated_by_section[n.receiver_section_id] = appreciated_by_section.get(n.receiver_section_id, 0) + 1
        top_teachers = {}
        for n in approved:
            if n.receiver_type == 'teacher' and n.receiver_teacher_id:
                top_teachers[n.receiver_teacher_id] = top_teachers.get(n.receiver_teacher_id, 0) + 1

        top_appreciators_section = sorted(appreciator_by_section.items(), key=lambda x: x[1], reverse=True)[:10]
        top_appreciated_section = sorted(appreciated_by_section.items(), key=lambda x: x[1], reverse=True)[:10]
        top_teachers = sorted(top_teachers.items(), key=lambda x: x[1], reverse=True)[:10]

        # Bar chart aggregations (Top 4 + Others) for Teachers, Students, Staffs
        from collections import defaultdict
        teacher_counts = defaultdict(int)
        staff_counts = defaultdict(int)
        student_counts = defaultdict(int)

        for n in approved:
            if n.receiver_type == 'teacher' and n.receiver_teacher:
                teacher_counts[n.receiver_teacher.name] += 1
            elif n.receiver_type == 'staff' and n.receiver_staff:
                staff_counts[n.receiver_staff.name] += 1
            elif n.receiver_type == 'student':
                label = None
                if n.receiver_student:
                    label = n.receiver_student.name
                elif n.receiver_name_text:
                    label = n.receiver_name_text
                if label:
                    student_counts[label] += 1

        def top_four_plus_others(counts_map):
            total = sum(counts_map.values()) or 1
            top = sorted(counts_map.items(), key=lambda x: x[1], reverse=True)[:4]
            items = []
            acc_pct = 0.0
            for name, cnt in top:
                pct = round(cnt * 100.0 / total, 1)
                acc_pct += pct
                items.append({"label": name, "pct": pct})
            others_pct = round(max(0.0, 100.0 - acc_pct), 1)
            if others_pct > 0 and len(counts_map) > len(top):
                items.append({"label": "Others", "pct": others_pct})
            return items

        bars = {
            "teachers": top_four_plus_others(teacher_counts),
            "students": top_four_plus_others(student_counts),
            "staffs": top_four_plus_others(staff_counts),
        }

        def sec_label(sec_id):
            sec = db.session.get(ClassSection, sec_id)
            return sec.label() if sec else "Unknown"

        def teacher_label(tid):
            t = db.session.get(Teacher, tid)
            return f"{t.name} — {t.subject}" if t else "Unknown"

        return render_template('leaders.html',
            top_appreciators_section=top_appreciators_section,
            top_appreciated_section=top_appreciated_section,
            top_teachers=top_teachers,
            sec_label=sec_label,
            teacher_label=teacher_label,
            bars=bars
        )

    # ---- Auth ----
    @app.route('/auth/register', methods=['GET','POST'])
    def auth_register():
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()
        if request.method == 'POST':
            role = (request.form.get('role') or '').strip()
            name = (request.form.get('name') or '').strip()
            username = (request.form.get('username') or '').strip().lower()
            password = request.form.get('password') or ''
            section_id = request.form.get('class_section_id') or None
            if role not in ('student','teacher','staff'):
                flash('Select a valid role', 'error'); return redirect(url_for('auth_register'))
            if not name or not username or not password:
                flash('Fill all required fields', 'error'); return redirect(url_for('auth_register'))
            if User.query.filter_by(username=username).first():
                flash('Username already taken', 'error'); return redirect(url_for('auth_register'))
            u = User(role=role, name=name, username=username)
            if role=='student' and section_id:
                u.class_section_id = int(section_id)
            u.set_password(password)
            db.session.add(u); db.session.commit()
            flash('Account created. Please sign in.', 'ok')
            return redirect(url_for('auth_login'))
        return render_template('auth_register.html', sections=sections)

    @app.route('/auth/login', methods=['GET','POST'])
    def auth_login():
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip().lower()
            password = request.form.get('password') or ''
            remember = True if request.form.get('remember')=='1' else False
            u = User.query.filter_by(username=username).first()
            if not u or not u.check_password(password):
                flash('Invalid credentials', 'error'); return redirect(url_for('auth_login'))
            session['uid'] = u.id
            if remember:
                session.permanent = True
                app.permanent_session_lifetime = timedelta(days=30)
            flash('Welcome back, '+u.name, 'ok')
            session['unlocked'] = True
            return redirect(url_for('home'))
        return render_template('auth_login.html')

    @app.route('/auth/logout')
    def auth_logout():
        session.pop('uid', None)
        session['unlocked'] = False
        flash('Signed out.', 'ok')
        return redirect(url_for('home'))

    @app.route('/lock', methods=['GET','POST'])
    def lock():
        if request.method == 'POST':
            # allow guest unlock once per session
            session['unlocked'] = True
            return redirect(url_for('home'))
        return render_template('lock.html')

    @app.route('/certificate/<category>/<int:rank>')
    def certificate(category, rank):
        # category: 'teacher'|'staff'|'student_section'
        # rank: 1..3
        if category not in ('teacher','staff','student_section') or rank not in (1,2,3):
            return redirect(url_for('leaders'))
        # Compute winners based on all-time bars similar to leaders
        approved = Note.query.filter_by(status='approved').all()
        if category == 'teacher':
            counts = {}
            for n in approved:
                if n.receiver_type == 'teacher' and n.receiver_teacher:
                    counts[n.receiver_teacher.name] = counts.get(n.receiver_teacher.name, 0) + 1
            items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            label = items[rank-1][0] if len(items) >= rank else None
        elif category == 'staff':
            counts = {}
            for n in approved:
                if n.receiver_type == 'staff' and n.receiver_staff:
                    counts[n.receiver_staff.name] = counts.get(n.receiver_staff.name, 0) + 1
            items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            label = items[rank-1][0] if len(items) >= rank else None
        else:
            # student_section appreciated
            counts = {}
            for n in approved:
                if n.receiver_type == 'student' and n.receiver_section_id:
                    counts[n.receiver_section_id] = counts.get(n.receiver_section_id, 0) + 1
            items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
            if len(items) >= rank:
                sec = db.session.get(ClassSection, items[rank-1][0])
                label = sec.label() if sec else None
            else:
                label = None
        if not label:
            flash('Not enough data for this certificate.', 'error')
            return redirect(url_for('leaders'))
        return render_template('certificate.html', category=category, rank=rank, label=label)

    @app.route('/admin/delete/<int:nid>')
    def admin_delete(nid):
        if not admin_required():
            return redirect(url_for('admin_login'))
        note = db.session.get(Note, nid)
        if note:
            db.session.delete(note)
            db.session.commit()
            flash('Note permanently deleted.', 'ok')
        return redirect(url_for('admin_panel'))

    # ---- Admin ----
    @app.route('/admin/login', methods=['GET','POST'])
    def admin_login():
        if request.method == 'POST':
            pin = request.form.get('pin')
            if pin == app.config['ADMIN_PIN']:
                session['admin_ok'] = True
                return redirect(url_for('admin_panel'))
            flash('Wrong PIN', 'error')
        return render_template('admin_login.html')

    @app.route('/admin/logout')
    def admin_logout():
        session.clear()
        return redirect(url_for('home'))

    @app.route('/admin', methods=['GET','POST'])
    def admin_panel():
        if not admin_required():
            return redirect(url_for('admin_login'))

        if request.method == 'POST':
            form_name = request.form.get('form')
            if form_name == 'section':
                c = (request.form.get('class_name') or '').strip()
                s = (request.form.get('section_name') or '').strip().upper()
                if c and s:
                    from sqlalchemy import and_
                    exists = db.session.query(ClassSection.id).filter(
                        and_(ClassSection.class_name==c, ClassSection.section_name==s)
                    ).first()
                    if not exists:
                        db.session.add(ClassSection(class_name=c, section_name=s))
                        db.session.commit()
                        flash('Section added.', 'ok')
                    else:
                        flash('Section exists.', 'error')
                return redirect(url_for('admin_panel'))
            elif form_name == 'teacher':
                tname = (request.form.get('teacher_name') or '').strip()
                tsubj = (request.form.get('teacher_subject') or '').strip()
                if tname and tsubj:
                    exists = Teacher.query.filter_by(name=tname, subject=tsubj).first()
                    if exists:
                        flash('Teacher already exists.', 'error')
                    else:
                        db.session.add(Teacher(name=tname, subject=tsubj))
                        db.session.commit()
                        flash('Teacher added.', 'ok')
                else:
                    flash('Please provide teacher name and subject.', 'error')
                return redirect(url_for('admin_panel'))

        pending = Note.query.filter_by(status='pending').order_by(Note.created_at.desc()).all()
        approved = Note.query.filter_by(status='approved').order_by(Note.created_at.desc()).all()
        hidden = Note.query.filter_by(status='hidden').order_by(Note.created_at.desc()).all()
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()
        teachers = Teacher.query.order_by(Teacher.name).all()
        staffers = Staff.query.order_by(Staff.name).all()
        return render_template('admin_panel.html',
            pending=pending, approved=approved, hidden=hidden,
            sections=sections, teachers=teachers, staffers=staffers)

    @app.route('/admin/note/<int:nid>/<action>')
    def admin_note_action(nid, action):
        if not admin_required():
            return redirect(url_for('admin_login'))
        note = db.session.get(Note, nid)
        if not note:
            return redirect(url_for('admin_panel'))
        if action == 'approve':
            note.status = 'approved'
        elif action == 'hide':
            note.status = 'hidden'
        elif action == 'unhide':
            note.status = 'approved'
        db.session.commit()
        return redirect(url_for('admin_panel'))

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)


