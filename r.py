import os  # import operating system utilities
from datetime import datetime, timedelta  # import date and time classes
from flask import Flask, render_template, request, redirect, url_for, flash, session  # import Flask and helper functions for web app
from flask_sqlalchemy import SQLAlchemy  # import SQLAlchemy ORM for database handling
from dotenv import load_dotenv  # import function to load environment variables from .env file

load_dotenv()  # load environment variables from .env file into the environment
db = SQLAlchemy()  # create SQLAlchemy database object (not yet bound to app)

def create_app():  # define application factory function
    app = Flask(__name__)  # create Flask app instance
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'change-me')  # set secret key for session and security, default fallback
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///kindness.db')  # set database connection URI, default to SQLite file
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # disable tracking modifications to save resources
    app.config['ADMIN_PIN'] = os.getenv('ADMIN_PIN', '1234')  # set admin PIN from environment variable, default '1234'
    db.init_app(app)  # initialize SQLAlchemy with app context

    # ------------------ MODELS ------------------
    class ClassSection(db.Model):  # define ClassSection model for class and section info
        id = db.Column(db.Integer, primary_key=True)  # primary key integer ID
        class_name = db.Column(db.String(10), nullable=False)  # class name string (e.g. "6")
        section_name = db.Column(db.String(10), nullable=False)  # section name string (e.g. "A")
        __table_args__ = (db.UniqueConstraint('class_name','section_name', name='uix_class_section'),)  # unique constraint on class and section
        def label(self):  # method to generate label string for class-section
            return f"{self.class_name}-{self.section_name}"  # return combined string e.g. "6-A"

    class Student(db.Model):  # define Student model
        id = db.Column(db.Integer, primary_key=True)  # primary key ID
        name = db.Column(db.String(120), nullable=False)  # student name string
        class_section_id = db.Column(db.Integer, db.ForeignKey('class_section.id'), nullable=False)  # foreign key to ClassSection
        class_section = db.relationship(ClassSection)  # relationship to ClassSection model

    class Teacher(db.Model):  # define Teacher model
        id = db.Column(db.Integer, primary_key=True)  # primary key ID
        name = db.Column(db.String(120), nullable=False)  # teacher name string
        subject = db.Column(db.String(120), nullable=False)  # subject taught by teacher

    class Staff(db.Model):  # define Staff model
        id = db.Column(db.Integer, primary_key=True)  # primary key ID
        name = db.Column(db.String(120), nullable=False)  # staff member name
        role = db.Column(db.String(120), nullable=False)  # staff role/position

    class Note(db.Model):  # define Note model for appreciation notes
        id = db.Column(db.Integer, primary_key=True)  # primary key ID

        # Giver fields
        giver_type = db.Column(db.String(20), nullable=False)  # giver type: 'student' or 'teacher'
        giver_name = db.Column(db.String(120))  # optional giver name (required if teacher)
        giver_section_id = db.Column(db.Integer, db.ForeignKey('class_section.id'))  # giver's class section ID
        giver_section = db.relationship(ClassSection, foreign_keys=[giver_section_id])  # relationship to giver's section

        # Receiver generic fields
        receiver_type = db.Column(db.String(20), nullable=False)  # receiver type: 'student', 'teacher', or 'staff'
        message = db.Column(db.Text, nullable=False)  # appreciation message text

        # Student receiver fields
        receiver_name_text = db.Column(db.String(120))  # free text student name
        receiver_section_id = db.Column(db.Integer, db.ForeignKey('class_section.id'))  # student receiver's section ID
        receiver_section = db.relationship(ClassSection, foreign_keys=[receiver_section_id])  # relationship to receiver section
        receiver_student_id = db.Column(db.Integer, db.ForeignKey('student.id'))  # student receiver foreign key ID
        receiver_student = db.relationship(Student)  # relationship to Student

        # Teacher receiver field
        receiver_teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'))  # teacher receiver foreign key ID
        receiver_teacher = db.relationship(Teacher)  # relationship to Teacher

        # Staff receiver field
        receiver_staff_id = db.Column(db.Integer, db.ForeignKey('staff.id'))  # staff receiver foreign key ID
        receiver_staff = db.relationship(Staff)  # relationship to Staff

        # Meta fields
        weight_applied = db.Column(db.Integer, default=1)  # weight of note, e.g. +1 same section, +2 cross section (student->student only)
        status = db.Column(db.String(20), default='pending')  # status: 'pending', 'approved', or 'hidden'
        created_at = db.Column(db.DateTime, default=datetime.utcnow)  # creation timestamp

    class Winner(db.Model):  # define Winner model for leaderboard winners
        id = db.Column(db.Integer, primary_key=True)  # primary key ID
        period_type = db.Column(db.String(20))  # period type: 'weekly', 'monthly', etc.
        category = db.Column(db.String(40))  # category type, e.g. 'top_student_manual'
        period_label = db.Column(db.String(40))  # label for the period (e.g. "Sep 2025")
        title = db.Column(db.String(200))  # title for winner
        description = db.Column(db.Text)  # description for winner
        created_at = db.Column(db.DateTime, default=datetime.utcnow)  # creation timestamp

    with app.app_context():  # run the following code within app context
        db.create_all()  # create database tables if they don't exist

        # lightweight auto-migration for Note columns if DB existed before
        conn = db.engine.connect()  # connect to database engine
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(note)").fetchall()]  # get existing columns of note table
        def add_col(name, ddl):  # helper function to add column if missing
            if name not in cols:  # if column not exists
                conn.exec_driver_sql(f"ALTER TABLE note ADD COLUMN {name} {ddl};")  # add column with SQL

        for col, ddl in [  # columns to add if missing
            ("receiver_type", "TEXT"),
            ("receiver_name_text", "TEXT"),
            ("receiver_section_id", "INTEGER"),
            ("receiver_teacher_id", "INTEGER"),
            ("receiver_staff_id", "INTEGER"),
        ]:
            add_col(col, ddl)  # call helper to add each column

        # Robust seed defaults (idempotent) for sections
        default_sections = [("6","A"),("7","A"),("7","B"),("8","A"),("8","B")]  # predefined class-section pairs
        for c, s in default_sections:
            exists = ClassSection.query.filter_by(class_name=c, section_name=s).first()  # check if exists
            if not exists:
                db.session.add(ClassSection(class_name=c, section_name=s))  # add if missing
        db.session.commit()  # commit changes

        # Seed default teachers
        default_teachers = [("Mr. Rahman","Mathematics"),("Ms. Ayesha","English"),
                            ("Mr. Kabir","Science"),("Mrs. Sultana","Bangla")]  # predefined teachers and subjects
        for name, subj in default_teachers:
            if not Teacher.query.filter_by(name=name, subject=subj).first():  # check if teacher exists
                db.session.add(Teacher(name=name, subject=subj))  # add teacher if missing
        db.session.commit()  # commit changes

        # Seed default staff
        default_staff = [("Anwar","Security"),("Rina","Cleaning"),("Masud","Office Assistant")]  # predefined staff names and roles
        for name, role in default_staff:
            if not Staff.query.filter_by(name=name, role=role).first():  # check if staff exists
                db.session.add(Staff(name=name, role=role))  # add if missing
        db.session.commit()  # commit changes

    # ------------------ AUTH ------------------
    def admin_required():  # function to check if admin is logged in
        return session.get('admin_ok') is True  # return True if admin_ok in session is True

    # ------------------ HELPERS ------------------
    def period_ranges():  # function returning date ranges for weekly, monthly, yearly, and all-time
        now = datetime.utcnow()  # current UTC datetime
        weekly_start = now - timedelta(days=7)  # 7 days ago
        monthly_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)  # start of current month
        yearly_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)  # start of year
        return {
            'weekly': weekly_start,
            'monthly': monthly_start,
            'yearly': yearly_start,
            'alltime': datetime(1970,1,1)  # epoch start as all-time
        }

    def compute_leaders():  # function to compute leaderboard winners per period and category
        # Delete existing winners before recomputing
        Winner.query.delete()  # delete all winners from DB
        db.session.commit()  # commit deletion

        periods = period_ranges()  # get period start datetimes

        for period, start_date in periods.items():  # for each period label and date
            # Query to get approved notes after period start date
            notes_q = Note.query.filter(Note.status == 'approved', Note.created_at >= start_date)

            # Aggregate points by receiver name text and section for students (manual)
            from sqlalchemy import func

            # Count notes for students by (receiver_name_text + receiver_section_id)
            student_points = notes_q.filter(Note.receiver_type == 'student').with_entities(
                Note.receiver_name_text,
                Note.receiver_section_id,
                func.count(Note.id).label('points')
            ).group_by(Note.receiver_name_text, Note.receiver_section_id).order_by(func.count(Note.id).desc()).limit(3).all()

            # Add winners for top student by manual input
            for i, (name, section_id, points) in enumerate(student_points, 1):
                sec = ClassSection.query.get(section_id)
                section_label = sec.label() if sec else "Unknown"
                winner = Winner(
                    period_type=period,
                    category='top_student_manual',
                    period_label=period,
                    title=f"Top Student #{i} ({section_label})",
                    description=f"{name} with {points} points"
                )
                db.session.add(winner)

            # TODO: similarly for teacher, staff, giver leaders...

        db.session.commit()  # commit all winners

    # ------------------ ROUTES ------------------

    @app.route('/')  # route for home page
    def index():
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()  # get all class sections ordered
        return render_template('index.html', sections=sections)  # render index template with sections

    @app.route('/wall')  # route for kindness wall view
    def wall():
        notes = Note.query.filter_by(status='approved').order_by(Note.created_at.desc()).limit(50).all()  # get last 50 approved notes
        return render_template('wall.html', notes=notes)  # render wall template with notes

    @app.route('/leaders')  # route for leaderboard view
    def leaders():
        winners = Winner.query.order_by(Winner.created_at.desc()).all()  # get all winners ordered by recent
        return render_template('leaders.html', winners=winners)  # render leaders template with winners

    @app.route('/post/student', methods=['GET', 'POST'])  # route to post a student appreciation note
    def post_student():
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()  # all sections for form dropdown
        if request.method == 'POST':  # if form submitted
            giver_type = request.form.get('giver_type', 'student').strip()  # get giver type from form or default 'student'
            giver_name = (request.form.get('giver_name') or '').strip()  # get giver name or empty string
            giver_section_id = request.form.get('giver_section_id')  # giver's section ID

            # Validate giver_name if giver_type is teacher
            if giver_type == 'teacher' and not giver_name:
                flash('Teacher giver must provide a name.', 'error')  # flash error message
                return redirect(request.url)  # reload page

            receiver_name = request.form.get('receiver_name', '').strip()  # student receiver name from form
            receiver_section_id = request.form.get('receiver_section_id')  # student receiver section ID
            message = request.form.get('message', '').strip()  # appreciation message

            if not receiver_name or not message:
                flash('Receiver name and message are required.', 'error')  # flash error if missing
                return redirect(request.url)

            # Calculate weight_applied based on giver and receiver sections
            weight = 1
            if giver_type == 'student' and giver_section_id and receiver_section_id and giver_section_id != receiver_section_id:
                weight = 2  # cross-section appreciation is weighted double

            note = Note(
                giver_type=giver_type,
                giver_name=giver_name or ('Anonymous' if giver_type == 'student' else ''),
                giver_section_id=giver_section_id,
                receiver_type='student',
                receiver_name_text=receiver_name,
                receiver_section_id=receiver_section_id,
                message=message,
                weight_applied=weight,
                status='pending'
            )
            db.session.add(note)  # add new note to DB
            db.session.commit()  # commit transaction

            flash('Note posted successfully and pending approval.', 'success')  # success message
            return redirect(url_for('wall'))  # redirect to wall view

        return render_template('post_student.html', sections=sections)  # GET request: show form

    @app.route('/post/teacher', methods=['GET', 'POST'])  # route to post a teacher appreciation note
    def post_teacher():
        teachers = Teacher.query.order_by(Teacher.name).all()  # get all teachers for form
        if request.method == 'POST':
            giver_type = request.form.get('giver_type', 'student').strip()
            giver_name = (request.form.get('giver_name') or '').strip()
            giver_section_id = request.form.get('giver_section_id')

            if giver_type == 'teacher' and not giver_name:
                flash('Teacher giver must provide a name.', 'error')
                return redirect(request.url)

            receiver_teacher_id = request.form.get('receiver_teacher_id')
            message = request.form.get('message', '').strip()

            if not receiver_teacher_id or not message:
                flash('Receiver teacher and message are required.', 'error')
                return redirect(request.url)

            note = Note(
                giver_type=giver_type,
                giver_name=giver_name or ('Anonymous' if giver_type == 'student' else ''),
                giver_section_id=giver_section_id,
                receiver_type='teacher',
                receiver_teacher_id=receiver_teacher_id,
                message=message,
                weight_applied=1,
                status='pending'
            )
            db.session.add(note)
            db.session.commit()
            flash('Note posted successfully and pending approval.', 'success')
            return redirect(url_for('wall'))
        return render_template('post_teacher.html', teachers=teachers)

    @app.route('/post/staff', methods=['GET', 'POST'])  # route to post a staff appreciation note
    def post_staff():
        staff_list = Staff.query.order_by(Staff.name).all()
        if request.method == 'POST':
            giver_type = request.form.get('giver_type', 'student').strip()
            giver_name = (request.form.get('giver_name') or '').strip()
            giver_section_id = request.form.get('giver_section_id')

            if giver_type == 'teacher' and not giver_name:
                flash('Teacher giver must provide a name.', 'error')
                return redirect(request.url)

            receiver_staff_id = request.form.get('receiver_staff_id')
            message = request.form.get('message', '').strip()

            if not receiver_staff_id or not message:
                flash('Receiver staff and message are required.', 'error')
                return redirect(request.url)

            note = Note(
                giver_type=giver_type,
                giver_name=giver_name or ('Anonymous' if giver_type == 'student' else ''),
                giver_section_id=giver_section_id,
                receiver_type='staff',
                receiver_staff_id=receiver_staff_id,
                message=message,
                weight_applied=1,
                status='pending'
            )
            db.session.add(note)
            db.session.commit()
            flash('Note posted successfully and pending approval.', 'success')
            return redirect(url_for('wall'))
        return render_template('post_staff.html', staff=staff_list)

    @app.route('/admin', methods=['GET', 'POST'])  # admin login route
    def admin_login():
        if request.method == 'POST':
            pin = request.form.get('pin')
            if pin == app.config['ADMIN_PIN']:  # check pin
                session['admin_ok'] = True  # set admin session flag
                flash('Admin login successful.', 'success')
                return redirect(url_for('admin_panel'))
            else:
                flash('Invalid PIN.', 'error')  # invalid pin message
        return render_template('admin_login.html')

    @app.route('/admin/panel')  # admin panel route
    def admin_panel():
        if not admin_required():
            return redirect(url_for('admin_login'))  # redirect if not logged in admin

        pending_notes = Note.query.filter_by(status='pending').order_by(Note.created_at.desc()).all()  # get all pending notes
        sections = ClassSection.query.order_by(ClassSection.class_name, ClassSection.section_name).all()
        return render_template('admin_panel.html', notes=pending_notes, sections=sections)  # render admin panel

    @app.route('/admin/approve/<int:note_id>')  # route to approve a note by ID
    def admin_approve(note_id):
        if not admin_required():
            return redirect(url_for('admin_login'))

        note = Note.query.get_or_404(note_id)
        note.status = 'approved'  # set status approved
        db.session.commit()
        flash('Note approved.', 'success')
        return redirect(url_for('admin_panel'))

    @app.route('/admin/hide/<int:note_id>')  # route to hide a note by ID
    def admin_hide(note_id):
        if not admin_required():
            return redirect(url_for('admin_login'))

        note = Note.query.get_or_404(note_id)
        note.status = 'hidden'  # set status hidden
        db.session.commit()
        flash('Note hidden.', 'success')
        return redirect(url_for('admin_panel'))

    @app.route('/admin/add_section', methods=['POST'])  # route to add a new class section
    def admin_add_section():
        if not admin_required():
            return redirect(url_for('admin_login'))

        class_name = request.form.get('class_name', '').strip()
        section_name = request.form.get('section_name', '').strip()
        if not class_name or not section_name:
            flash('Class and Section are required.', 'error')
            return redirect(url_for('admin_panel'))

        existing = ClassSection.query.filter_by(class_name=class_name, section_name=section_name).first()
        if existing:
            flash('Section already exists.', 'error')
        else:
            new_section = ClassSection(class_name=class_name, section_name=section_name)
            db.session.add(new_section)
            db.session.commit()
            flash('New section added.', 'success')
        return redirect(url_for('admin_panel'))

    @app.route('/admin/logout')  # admin logout route
    def admin_logout():
        session.pop('admin_ok', None)  # remove admin session flag
        flash('Logged out.', 'success')
        return redirect(url_for('index'))

    return app  # return the Flask app instance
