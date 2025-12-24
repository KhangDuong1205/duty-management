from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, date
import pandas as pd
import os
import random
from werkzeug.utils import secure_filename
from collections import defaultdict

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///duty_management.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'xlsx', 'xls', 'csv'}

db = SQLAlchemy(app)

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database Models
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.String(20), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    grade = db.Column(db.Integer)
    gender = db.Column(db.String(10))
    country = db.Column(db.String(50))
    table_number = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    duty_assignments = db.relationship('DutyAssignment', backref='student', lazy=True)

    def __repr__(self):
        return f'<Student {self.student_id}: {self.full_name}>'

class Table(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    table_number = db.Column(db.Integer, unique=True, nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    current_count = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<Table {self.table_number} - Capacity: {self.capacity}>'

class Duty(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    duty_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    meal_type = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)

    assignments = db.relationship('DutyAssignment', backref='duty', lazy=True)

    def __repr__(self):
        return f'<Duty {self.duty_name}>'

class DutyAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    duty_id = db.Column(db.Integer, db.ForeignKey('duty.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    meal_type = db.Column(db.String(20))
    completed = db.Column(db.Boolean, default=False)
    table_number = db.Column(db.Integer)

    def __repr__(self):
        return f'<DutyAssignment Student:{self.student_id} Duty:{self.duty_id} Date:{self.date}>'

class Term(db.Model):
    """Academic term for duty scheduling"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    weeks = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    weekly_assignments = db.relationship('WeeklyDutyAssignment', backref='term', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Term {self.name}: {self.weeks} weeks>'

class WeeklyDutyAssignment(db.Model):
    """Which table is on duty for which week"""
    id = db.Column(db.Integer, primary_key=True)
    term_id = db.Column(db.Integer, db.ForeignKey('term.id'), nullable=False)
    week_number = db.Column(db.Integer, nullable=False)
    table_number = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)

    daily_duties = db.relationship('DailyDuty', backref='weekly_assignment', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<WeeklyDuty Week:{self.week_number} Table:{self.table_number}>'

class DailyDuty(db.Model):
    """Specific duty assignment for AM/PM each day"""
    id = db.Column(db.Integer, primary_key=True)
    weekly_assignment_id = db.Column(db.Integer, db.ForeignKey('weekly_duty_assignment.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    shift = db.Column(db.String(2), nullable=False)  # 'AM' or 'PM'
    student1_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)
    student2_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=True)

    student1 = db.relationship('Student', foreign_keys=[student1_id])
    student2 = db.relationship('Student', foreign_keys=[student2_id])

    def __repr__(self):
        return f'<DailyDuty {self.date} {self.shift}>'

# Helper Functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def create_duty_schedule_for_term(term_id):
    """Generate complete duty schedule for a term"""
    term = Term.query.get(term_id)
    if not term:
        return False, "Term not found"

    # Get all tables
    tables = Table.query.order_by(Table.table_number).all()
    if not tables:
        return False, "No tables available"

    # Clear existing assignments for this term
    WeeklyDutyAssignment.query.filter_by(term_id=term_id).delete()

    # Assign tables to weeks (round-robin)
    current_date = term.start_date
    week_assignments = []

    for week_num in range(1, term.weeks + 1):
        # Calculate week start and end
        week_start = current_date
        week_end = week_start + timedelta(days=6)

        # Assign table (rotate through tables)
        table = tables[(week_num - 1) % len(tables)]

        # Create weekly assignment
        weekly_assignment = WeeklyDutyAssignment(
            term_id=term_id,
            week_number=week_num,
            table_number=table.table_number,
            start_date=week_start,
            end_date=week_end
        )
        db.session.add(weekly_assignment)
        db.session.flush()  # Get the ID

        # Get students from this table
        table_students = Student.query.filter_by(table_number=table.table_number).all()

        if not table_students:
            current_date = week_end + timedelta(days=1)
            continue

        # Create daily duties for this week (7 days × 2 shifts = 14 duties)
        # Each duty needs 2 students, so 28 student slots total
        student_pool = table_students.copy()
        random.shuffle(student_pool)

        # If not enough students, repeat the list
        while len(student_pool) < 28:
            student_pool.extend(table_students)

        student_index = 0

        for day_offset in range(7):
            duty_date = week_start + timedelta(days=day_offset)

            for shift in ['AM', 'PM']:
                # Assign 2 students to this duty
                student1 = student_pool[student_index % len(student_pool)]
                student_index += 1
                student2 = student_pool[student_index % len(student_pool)]
                student_index += 1

                daily_duty = DailyDuty(
                    weekly_assignment_id=weekly_assignment.id,
                    date=duty_date,
                    shift=shift,
                    student1_id=student1.id,
                    student2_id=student2.id
                )
                db.session.add(daily_duty)

        current_date = week_end + timedelta(days=1)

    try:
        db.session.commit()
        return True, f"Successfully created duty schedule for {term.weeks} weeks"
    except Exception as e:
        db.session.rollback()
        return False, f"Error: {str(e)}"

def get_student_duty_count(student_id, term_id):
    """Count how many duties a student has in a term"""
    weekly_assignments = WeeklyDutyAssignment.query.filter_by(term_id=term_id).all()
    weekly_ids = [wa.id for wa in weekly_assignments]

    count = DailyDuty.query.filter(
        DailyDuty.weekly_assignment_id.in_(weekly_ids),
        ((DailyDuty.student1_id == student_id) | (DailyDuty.student2_id == student_id))
    ).count()

    return count

def distribute_students_to_tables_smart():
    """Advanced algorithm for smart and balanced student distribution"""
    students = Student.query.filter_by(table_number=None).all()
    tables = Table.query.order_by(Table.table_number).all()

    if not tables:
        flash('No tables available. Please create tables first.', 'error')
        return False

    if not students:
        flash('No unassigned students to distribute.', 'info')
        return True

    random.shuffle(students)

    total_capacity = sum(t.capacity for t in tables)
    total_students = len(students)

    table_assignments = {table.table_number: [] for table in tables}
    table_capacities = {table.table_number: table.capacity for table in tables}

    by_grade = defaultdict(list)
    by_gender = defaultdict(list)
    by_country = defaultdict(list)

    for student in students:
        grade = student.grade if student.grade else 'unknown'
        gender = student.gender if student.gender else 'unknown'
        country = student.country if student.country else 'unknown'

        by_grade[grade].append(student)
        by_gender[gender].append(student)
        by_country[country].append(student)

    grade_keys = list(by_grade.keys())
    random.shuffle(grade_keys)

    assigned_students = set()

    for grade in grade_keys:
        grade_students = [s for s in by_grade[grade] if s not in assigned_students]
        random.shuffle(grade_students)

        for student in grade_students:
            available_tables = list(table_assignments.keys())

            if not available_tables:
                break

            best_table = None
            best_score = float('inf')

            for table_num in available_tables:
                current_students = table_assignments[table_num]
                same_grade = sum(1 for s in current_students if s.grade == student.grade)
                same_gender = sum(1 for s in current_students if s.gender == student.gender)
                same_country = sum(1 for s in current_students if s.country == student.country)

                score = (same_grade * 3) + (same_gender * 2) + (same_country * 2)
                score += random.uniform(0, 2)

                

                if score < best_score:
                    best_score = score
                    best_table = table_num

            if best_table:
                table_assignments[best_table].append(student)
                assigned_students.add(student)

    remaining_students = [s for s in students if s not in assigned_students]
    random.shuffle(remaining_students)

    table_list = list(table_assignments.keys())
    for idx, student in enumerate(remaining_students):
        table_num = table_list[idx % len(table_list)]
        table_assignments[table_num].append(student)
        assigned_students.add(student)

    for table_num, students_list in table_assignments.items():
        for student in students_list:
            student.table_number = table_num

    for table in tables:
        count = sum(1 for s in students if s.table_number == table.table_number)
        table.current_count = count

    try:
        db.session.commit()
        flash(f'Successfully distributed {len(assigned_students)} students with balanced diversity!', 'success')
        return True
    except Exception as e:
        db.session.rollback()
        flash(f'Error during distribution: {str(e)}', 'error')
        return False

# Routes
@app.route('/')
def index():
    total_students = Student.query.count()
    tables = Table.query.order_by(Table.table_number).all()
    total_duties = Duty.query.filter_by(is_active=True).count()
    assigned_students = Student.query.filter(Student.table_number.isnot(None)).count()
    active_term = Term.query.filter_by(is_active=True).first()

    return render_template('index.html', 
                         total_students=total_students,
                         tables=tables,
                         total_duties=total_duties,
                         assigned_students=assigned_students,
                         active_term=active_term)

@app.route('/students')
def students():
    sort_by = request.args.get('sort', 'student_id')
    order = request.args.get('order', 'asc')
    filter_table = request.args.get('table', '')
    filter_grade = request.args.get('grade', '')
    filter_country = request.args.get('country', '')
    search_query = request.args.get('search', '')

    query = Student.query

    if filter_table:
        if filter_table == 'unassigned':
            query = query.filter(Student.table_number.is_(None))
        else:
            query = query.filter_by(table_number=int(filter_table))

    if filter_grade:
        query = query.filter_by(grade=int(filter_grade))

    if filter_country:
        query = query.filter_by(country=filter_country)

    if search_query:
        search_pattern = f'%{search_query}%'
        query = query.filter(
            (Student.full_name.ilike(search_pattern)) | 
            (Student.student_id.ilike(search_pattern))
        )

    if sort_by == 'name':
        if order == 'desc':
            query = query.order_by(Student.full_name.desc())
        else:
            query = query.order_by(Student.full_name.asc())
    elif sort_by == 'grade':
        if order == 'desc':
            query = query.order_by(Student.grade.desc().nullslast())
        else:
            query = query.order_by(Student.grade.asc().nullsfirst())
    elif sort_by == 'table':
        if order == 'desc':
            query = query.order_by(Student.table_number.desc().nullslast())
        else:
            query = query.order_by(Student.table_number.asc().nullsfirst())
    elif sort_by == 'country':
        if order == 'desc':
            query = query.order_by(Student.country.desc().nullslast())
        else:
            query = query.order_by(Student.country.asc().nullsfirst())
    else:
        if order == 'desc':
            query = query.order_by(Student.student_id.desc())
        else:
            query = query.order_by(Student.student_id.asc())

    all_students = query.all()
    tables = Table.query.order_by(Table.table_number).all()

    all_grades = db.session.query(Student.grade).distinct().filter(Student.grade.isnot(None)).order_by(Student.grade).all()
    all_countries = db.session.query(Student.country).distinct().filter(Student.country.isnot(None)).order_by(Student.country).all()

    return render_template('students.html', 
                         students=all_students, 
                         tables=tables,
                         grades=[g[0] for g in all_grades],
                         countries=[c[0] for c in all_countries],
                         current_sort=sort_by,
                         current_order=order,
                         current_table_filter=filter_table,
                         current_grade_filter=filter_grade,
                         current_country_filter=filter_country,
                         search_query=search_query)

@app.route('/change_student_table/<int:student_id>', methods=['POST'])
def change_student_table(student_id):
    student = Student.query.get_or_404(student_id)
    new_table_number = request.form.get('table_number', type=int)

    old_table_number = student.table_number

    if new_table_number == 0:
        student.table_number = None
    else:
        table = Table.query.filter_by(table_number=new_table_number).first()
        if not table:
            flash(f'Table {new_table_number} does not exist', 'error')
            return redirect(url_for('students'))

        student.table_number = new_table_number

    try:
        db.session.commit()

        if old_table_number:
            old_table = Table.query.filter_by(table_number=old_table_number).first()
            if old_table:
                old_table.current_count = Student.query.filter_by(table_number=old_table_number).count()

        if new_table_number and new_table_number != 0:
            new_table = Table.query.filter_by(table_number=new_table_number).first()
            if new_table:
                new_table.current_count = Student.query.filter_by(table_number=new_table_number).count()

        db.session.commit()
        flash(f'Student {student.student_id} moved to Table {new_table_number if new_table_number != 0 else "Unassigned"}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error changing table: {str(e)}', 'error')

    return redirect(url_for('students'))

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file uploaded', 'error')
            return redirect(request.url)

        file = request.files['file']

        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)

            try:
                df = pd.read_excel(filepath)

                required_columns = ['Student ID', 'Full Name']
                if not all(col in df.columns for col in required_columns):
                    flash(f'Excel file must contain columns: {", ".join(required_columns)}', 'error')
                    return redirect(request.url)

                imported_count = 0
                for _, row in df.iterrows():
                    student_id = str(row['Student ID']).strip()

                    existing_student = Student.query.filter_by(student_id=student_id).first()
                    if existing_student:
                        continue

                    student = Student(
                        student_id=student_id,
                        full_name=str(row['Full Name']).strip(),
                        grade=int(row['Grade']) if 'Grade' in df.columns and pd.notna(row['Grade']) else None,
                        gender=str(row['Gender']).strip() if 'Gender' in df.columns and pd.notna(row['Gender']) else None,
                        country=str(row['Country']).strip() if 'Country' in df.columns and pd.notna(row['Country']) else None
                    )
                    db.session.add(student)
                    imported_count += 1

                db.session.commit()

                if Table.query.count() > 0:
                    distribute_students_to_tables_smart()
                else:
                    flash(f'Successfully imported {imported_count} students. Create tables to assign students.', 'info')

                return redirect(url_for('students'))

            except Exception as e:
                db.session.rollback()
                flash(f'Error importing file: {str(e)}', 'error')
                return redirect(request.url)
        else:
            flash('Invalid file type. Please upload .xlsx or .xls file', 'error')
            return redirect(request.url)

    return render_template('upload.html')

@app.route('/tables')
def tables():
    all_tables = Table.query.order_by(Table.table_number).all()

    table_data = []
    for table in all_tables:
        students = Student.query.filter_by(table_number=table.table_number).all()
        table_data.append({
            'table': table,
            'students': students
        })

    total_capacity = sum(t.capacity for t in all_tables)
    total_assigned = sum(len(td['students']) for td in table_data)

    return render_template('tables.html', 
                         table_data=table_data,
                         total_capacity=total_capacity,
                         total_assigned=total_assigned)

@app.route('/tables/add', methods=['GET', 'POST'])
def add_table():
    if request.method == 'POST':
        table_number = request.form.get('table_number', type=int)
        capacity = request.form.get('capacity', type=int)

        if not table_number or not capacity:
            flash('Table number and capacity are required', 'error')
            return redirect(request.url)

        if capacity <= 0:
            flash('Capacity must be greater than 0', 'error')
            return redirect(request.url)

        existing_table = Table.query.filter_by(table_number=table_number).first()
        if existing_table:
            flash(f'Table {table_number} already exists', 'error')
            return redirect(request.url)

        new_table = Table(table_number=table_number, capacity=capacity)
        db.session.add(new_table)

        try:
            db.session.commit()
            flash(f'Table {table_number} added successfully with capacity {capacity}', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding table: {str(e)}', 'error')

        return redirect(url_for('tables'))

    return render_template('add_table.html')

@app.route('/tables/edit/<int:id>', methods=['GET', 'POST'])
def edit_table(id):
    table = Table.query.get_or_404(id)

    if request.method == 'POST':
        new_table_number = request.form.get('table_number', type=int)
        new_capacity = request.form.get('capacity', type=int)

        if not new_table_number or not new_capacity:
            flash('Table number and capacity are required', 'error')
            return redirect(request.url)

        if new_capacity <= 0:
            flash('Capacity must be greater than 0', 'error')
            return redirect(request.url)

        if new_table_number != table.table_number:
            existing_table = Table.query.filter_by(table_number=new_table_number).first()
            if existing_table:
                flash(f'Table {new_table_number} already exists', 'error')
                return redirect(request.url)

        if new_table_number != table.table_number:
            students = Student.query.filter_by(table_number=table.table_number).all()
            for student in students:
                student.table_number = new_table_number

        table.table_number = new_table_number
        table.capacity = new_capacity

        student_count = len(Student.query.filter_by(table_number=new_table_number).all())
        table.current_count = student_count

        try:
            db.session.commit()
            flash(f'Table {new_table_number} updated successfully', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating table: {str(e)}', 'error')

        return redirect(url_for('tables'))

    return render_template('edit_table.html', table=table)

@app.route('/tables/delete/<int:id>')
def delete_table(id):
    table = Table.query.get_or_404(id)

    students = Student.query.filter_by(table_number=table.table_number).all()
    for student in students:
        student.table_number = None

    db.session.delete(table)

    try:
        db.session.commit()
        flash(f'Table {table.table_number} deleted. Students have been unassigned.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting table: {str(e)}', 'error')

    return redirect(url_for('tables'))

@app.route('/duties')
def duties():
    all_duties = Duty.query.filter_by(is_active=True).all()
    return render_template('duties.html', duties=all_duties)

@app.route('/redistribute')
def redistribute():
    if Table.query.count() == 0:
        flash('No tables available. Please create tables first.', 'error')
        return redirect(url_for('tables'))

    students = Student.query.all()
    for student in students:
        student.table_number = None

    tables = Table.query.all()
    for table in tables:
        table.current_count = 0

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing assignments: {str(e)}', 'error')
        return redirect(url_for('tables'))

    distribute_students_to_tables_smart()

    return redirect(url_for('tables'))

@app.route('/clear_students')
def clear_students():
    try:
        Student.query.delete()
        db.session.commit()
        flash('All students have been cleared!', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error clearing students: {str(e)}', 'error')

    return redirect(url_for('students'))

# DUTY SCHEDULING ROUTES

@app.route('/duty_schedule')
def duty_schedule():
    """Main duty schedule page showing weekly view"""
    terms = Term.query.order_by(Term.start_date.desc()).all()
    active_term = Term.query.filter_by(is_active=True).first()

    # Get week parameter from URL or default to current week
    week_num = request.args.get('week', type=int, default=1)
    term_id = request.args.get('term', type=int)

    if term_id:
        selected_term = Term.query.get(term_id)
    else:
        selected_term = active_term

    if not selected_term:
        return render_template('duty_schedule.html', terms=terms, selected_term=None, week_data=None)

    # Get weekly assignment for this week
    weekly_assignment = WeeklyDutyAssignment.query.filter_by(
        term_id=selected_term.id,
        week_number=week_num
    ).first()

    week_data = None
    if weekly_assignment:
        # Get all daily duties for this week
        daily_duties = DailyDuty.query.filter_by(
            weekly_assignment_id=weekly_assignment.id
        ).order_by(DailyDuty.date, DailyDuty.shift).all()

        # Organise by day
        duties_by_day = {}
        for duty in daily_duties:
            day_key = duty.date.strftime('%Y-%m-%d')
            if day_key not in duties_by_day:
                duties_by_day[day_key] = {'date': duty.date, 'AM': None, 'PM': None}
            duties_by_day[day_key][duty.shift] = duty

        week_data = {
            'weekly_assignment': weekly_assignment,
            'duties_by_day': duties_by_day,
            'sorted_dates': sorted(duties_by_day.keys())
        }

    return render_template('duty_schedule.html', 
                         terms=terms,
                         selected_term=selected_term,
                         current_week=week_num,
                         week_data=week_data)

@app.route('/duty_schedule/analytics')
def duty_analytics():
    """Analytics page showing duty counts per student"""
    active_term = Term.query.filter_by(is_active=True).first()

    if not active_term:
        flash('No active term found. Please create a term first.', 'info')
        return redirect(url_for('manage_terms'))

    # Get all students and their duty counted
    students = Student.query.filter(Student.table_number.isnot(None)).order_by(Student.table_number, Student.full_name).all()

    student_stats = []
    for student in students:
        duty_count = get_student_duty_count(student.id, active_term.id)
        student_stats.append({
            'student': student,
            'duty_count': duty_count,
            'expected': active_term.weeks,  # Approximately 1 duty per week
            'status': 'on_track' if duty_count >= active_term.weeks - 1 else 'under'
        })

    return render_template('duty_analytics.html', 
                         term=active_term,
                         student_stats=student_stats)

@app.route('/manage_terms')
def manage_terms():
    """Manage academic terms"""
    terms = Term.query.order_by(Term.start_date.desc()).all()
    return render_template('manage_terms.html', terms=terms)

@app.route('/terms/add', methods=['GET', 'POST'])
def add_term():
    """Add new academic term"""
    if request.method == 'POST':
        name = request.form.get('name')
        start_date_str = request.form.get('start_date')
        weeks = request.form.get('weeks', type=int)

        if not all([name, start_date_str, weeks]):
            flash('All fields are required', 'error')
            return redirect(request.url)

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = start_date + timedelta(weeks=weeks)

            # Deactivate 
            is_active = request.form.get('is_active') == 'on'
            if is_active:
                Term.query.update({'is_active': False})

            term = Term(
                name=name,
                start_date=start_date,
                end_date=end_date,
                weeks=weeks,
                is_active=is_active
            )
            db.session.add(term)
            db.session.commit()

            flash(f'Term "{name}" created successfully', 'success')
            return redirect(url_for('manage_terms'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error creating term: {str(e)}', 'error')
            return redirect(request.url)

    return render_template('add_term.html')

@app.route('/terms/generate_schedule/<int:term_id>')
def generate_schedule(term_id):
    """Generate duty schedule for a term"""
    success, message = create_duty_schedule_for_term(term_id)

    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')

    return redirect(url_for('duty_schedule', term=term_id))

@app.route('/terms/delete/<int:id>')
def delete_term(id):
    """Delete a term and all its assignments"""
    term = Term.query.get_or_404(id)

    try:
        db.session.delete(term)
        db.session.commit()
        flash(f'Term "{term.name}" deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting term: {str(e)}', 'error')

    return redirect(url_for('manage_terms'))


@app.route('/duty/update', methods=['POST'])
def update_duty():
    """AJAX endpoint to update duty assignments via drag-and-drop"""
    data = request.get_json()

    duty_id = data.get('duty_id')
    slot = data.get('slot')  # 'student1' or 'student2'
    student_id = data.get('student_id')  # Can be None to clear

    try:
        duty = DailyDuty.query.get(duty_id)
        if not duty:
            return jsonify({'success': False, 'error': 'Duty not found'}), 404

        # Update the appropriate slot
        if slot == 'student1':
            duty.student1_id = student_id if student_id else None
        elif slot == 'student2':
            duty.student2_id = student_id if student_id else None
        else:
            return jsonify({'success': False, 'error': 'Invalid slot'}), 400

        db.session.commit()

        # Return updated student info
        student_info = None
        if student_id:
            student = Student.query.get(student_id)
            if student:
                student_info = {
                    'id': student.id,
                    'student_id': student.student_id,
                    'full_name': student.full_name
                }

        return jsonify({
            'success': True,
            'student_info': student_info
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/duty/regenerate_week', methods=['POST'])
def regenerate_week():
    """AJAX endpoint to regenerate duty assignments for a single week"""
    data = request.get_json()
    weekly_assignment_id = data.get('weekly_assignment_id')

    try:
        weekly_assignment = WeeklyDutyAssignment.query.get(weekly_assignment_id)
        if not weekly_assignment:
            return jsonify({'success': False, 'error': 'Weekly assignment not found'}), 404

        # Get students from the assigned table
        table_students = Student.query.filter_by(
            table_number=weekly_assignment.table_number
        ).all()

        if not table_students:
            return jsonify({'success': False, 'error': 'No students in this table'}), 400

        # Clear existing daily duties for this week
        DailyDuty.query.filter_by(weekly_assignment_id=weekly_assignment_id).delete()

        # Shuffle students for random assignment
        student_pool = table_students.copy()
        random.shuffle(student_pool)

        # If not enough students, repeat the list
        while len(student_pool) < 28:  # 7 days × 2 shifts × 2 students
            student_pool.extend(table_students)

        student_index = 0

        # Generate duties for 7 days
        for day_offset in range(7):
            duty_date = weekly_assignment.start_date + timedelta(days=day_offset)

            for shift in ['AM', 'PM']:
                student1 = student_pool[student_index % len(student_pool)]
                student_index += 1
                student2 = student_pool[student_index % len(student_pool)]
                student_index += 1

                daily_duty = DailyDuty(
                    weekly_assignment_id=weekly_assignment.id,
                    date=duty_date,
                    shift=shift,
                    student1_id=student1.id,
                    student2_id=student2.id
                )
                db.session.add(daily_duty)

        db.session.commit()
        return jsonify({'success': True, 'message': 'Week regenerated successfully'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
