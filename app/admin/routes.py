"""
Admin routes - narratives Version 1.1:
  2.2 Manage Exam Schedule  (create / view / update / delete batches)
  2.3 View Attendance       (read-only list, batch filter, CSV export)
Login (2.1) lives in auth.py.
"""
import csv
import io
from datetime import datetime, date, timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, Response)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import ExamBatch, Applicant, Attendance

admin_bp = Blueprint('admin', __name__)


# ==========================================================================
# DASHBOARD
# ==========================================================================

@admin_bp.route('/dashboard')
@login_required
def dashboard():
    day_start = datetime.combine(date.today(), datetime.min.time())
    day_end = day_start + timedelta(days=1)

    registrations_today = Applicant.query.filter(
        Applicant.registered_at >= day_start,
        Applicant.registered_at < day_end).count()

    timed_in_today = Attendance.query.filter(
        Attendance.timed_in_at >= day_start,
        Attendance.timed_in_at < day_end).count()

    all_batches = (ExamBatch.query
                   .order_by(ExamBatch.exam_date, ExamBatch.start_time).all())
    open_batches = sum(1 for b in all_batches if b.is_open)
    upcoming_batches = [b for b in all_batches if not b.has_ended][:5]

    recent_attendance = (Attendance.query
                         .order_by(Attendance.timed_in_at.desc())
                         .limit(6).all())

    return render_template(
        'admin/dashboard.html',
        registrations_today=registrations_today,
        timed_in_today=timed_in_today,
        open_batches=open_batches,
        upcoming_batches=upcoming_batches,
        recent_attendance=recent_attendance,
    )


# ==========================================================================
# 2.2 MANAGE EXAM SCHEDULE
# ==========================================================================

def _read_batch_form():
    """
    Read and validate the batch form (Step 4).
    Returns (values_dict, error_message). error_message is None when valid.
    """
    try:
        exam_date = datetime.strptime(request.form.get('exam_date', ''), '%Y-%m-%d').date()
        start_time = datetime.strptime(request.form.get('start_time', ''), '%H:%M').time()
        end_time = datetime.strptime(request.form.get('end_time', ''), '%H:%M').time()
        capacity = int(request.form.get('capacity', ''))
    except ValueError:
        return None, "Please fill in the date, start and end time, and capacity correctly."

    building = request.form.get('building', '').strip()
    room = request.form.get('room', '').strip()

    if not building or not room:
        return None, "Building and room are required."
    if end_time <= start_time:
        return None, "End time must be after the start time."
    if capacity < 1:
        return None, "Capacity must be at least 1."

    return {
        'exam_date': exam_date,
        'start_time': start_time,
        'end_time': end_time,
        'building': building,
        'room': room,
        'capacity': capacity,
    }, None


def _blocked_reason(batch):
    """Why a batch can't be updated/deleted, or None if it can (2.2)."""
    if batch.is_ongoing:
        return "This batch is ongoing, so it can't be changed."
    if batch.reserved_seats > 0:
        return ("This batch already has applicants assigned or reserved, "
                "so it can't be changed.")
    return None


@admin_bp.route('/batches', methods=['GET', 'POST'])
@login_required
def batches():
    if request.method == 'POST':
        values, error = _read_batch_form()
        if error:
            flash(error)
            return redirect(url_for('admin.batches'))

        # The batch ID is assigned automatically by the database.
        db.session.add(ExamBatch(created_by=current_user.id, **values))
        db.session.commit()
        flash("Batch created.")
        return redirect(url_for('admin.batches'))

    all_batches = (ExamBatch.query
                   .order_by(ExamBatch.exam_date, ExamBatch.start_time).all())
    return render_template('admin/batches.html', batches=all_batches)


@admin_bp.route('/batches/<int:batch_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_batch(batch_id):
    batch = ExamBatch.query.get_or_404(batch_id)

    reason = _blocked_reason(batch)
    if reason:
        flash(reason)
        return redirect(url_for('admin.batches'))

    if request.method == 'POST':
        values, error = _read_batch_form()
        if error:
            flash(error)
            return redirect(url_for('admin.edit_batch', batch_id=batch.id))

        for field, value in values.items():
            setattr(batch, field, value)
        db.session.commit()
        flash("Batch updated.")
        return redirect(url_for('admin.batches'))

    return render_template('admin/edit_batch.html', batch=batch)


@admin_bp.route('/batches/<int:batch_id>/delete', methods=['POST'])
@login_required
def delete_batch(batch_id):
    batch = ExamBatch.query.get_or_404(batch_id)

    reason = _blocked_reason(batch)
    if reason:
        flash(reason)
        return redirect(url_for('admin.batches'))

    db.session.delete(batch)
    db.session.commit()
    flash("Batch deleted.")
    return redirect(url_for('admin.batches'))


# ==========================================================================
# 2.3 VIEW ATTENDANCE  (read-only)
# ==========================================================================

def _attendance_query(batch_id):
    """Attendance rows, optionally filtered by the applicant's batch."""
    query = Attendance.query.join(Applicant, Attendance.applicant_id == Applicant.id)
    if batch_id:
        query = query.filter(Applicant.exam_batch_id == batch_id)
    return query.order_by(Attendance.timed_in_at.desc())


@admin_bp.route('/attendance')
@login_required
def attendance():
    batch_filter = request.args.get('batch_id', type=int)
    records = _attendance_query(batch_filter).all()
    all_batches = (ExamBatch.query
                   .order_by(ExamBatch.exam_date, ExamBatch.start_time).all())
    selected_batch = db.session.get(ExamBatch, batch_filter) if batch_filter else None

    # The template shows a "No records" message when `records` is empty.
    return render_template(
        'admin/attendance.html',
        records=records,
        batches=all_batches,
        batch_filter=batch_filter,
        selected_batch=selected_batch,
    )


def _csv_safe(value):
    """Stop spreadsheet formula injection (a name like '=1+1' opening as a formula)."""
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text


@admin_bp.route('/attendance/export')
@login_required
def export_attendance():
    batch_filter = request.args.get('batch_id', type=int)
    records = _attendance_query(batch_filter).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Full Name', 'Registration Number', 'Batch ID',
                     'Time-In Timestamp', 'Reference Number'])
    for r in records:
        writer.writerow([
            _csv_safe(r.applicant.full_name),
            r.applicant.registration_number,
            r.applicant.exam_batch_id,
            r.timed_in_at.strftime('%Y-%m-%d %H:%M:%S'),
            r.reference_number,
        ])

    filename = f"attendance_batch_{batch_filter}.csv" if batch_filter else "attendance_all.csv"
    return Response(
        '\ufeff' + buffer.getvalue(),   # BOM so Excel reads UTF-8 names correctly
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )