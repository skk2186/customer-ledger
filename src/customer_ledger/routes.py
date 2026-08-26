"""HTTP routes for the stage-one customer management loop."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .customer_service import (
    BusinessError,
    archive_customer,
    create_customer,
    restore_customer,
    update_customer,
)
from .extensions import db
from .models import Customer
from .validation import ValidationError

main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    return render_template("index.html")


@main_bp.get("/healthz")
def healthz():
    return {"status": "ok"}


def _customer_or_404(customer_id: int) -> Customer:
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        abort(404)
    return customer


def _save_customer(operation, *args):
    try:
        customer = operation(db.session, *args)
        db.session.commit()
        return customer, None
    except (BusinessError, ValidationError) as exc:
        db.session.rollback()
        return None, str(exc)
    except IntegrityError:
        db.session.rollback()
        return None, "客户保存失败，请检查名称是否重复。"
    except SQLAlchemyError:
        db.session.rollback()
        return None, "客户保存失败，请稍后重试。"


@main_bp.get("/customers")
def customers():
    query = request.args.get("q", "").strip()
    statement = select(Customer).order_by(Customer.active.desc(), Customer.name.asc())
    if query:
        statement = statement.where(
            or_(Customer.name.contains(query), Customer.normalized_name.contains(query.casefold()))
        )
    customer_list = db.session.scalars(statement).all()
    return render_template("customers.html", customers=customer_list, query=query)


@main_bp.route("/customers/new", methods=["GET", "POST"])
def new_customer():
    values = {"name": "", "notes": ""}
    error = None
    if request.method == "POST":
        values = {"name": request.form.get("name", ""), "notes": request.form.get("notes", "")}
        _, error = _save_customer(create_customer, values["name"], values["notes"])
        if error is None:
            flash("客户已新增。", "success")
            return redirect(url_for("main.customers"))
    return render_template("customer_form.html", customer=None, values=values, error=error)


@main_bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def edit_customer(customer_id: int):
    customer = _customer_or_404(customer_id)
    values = {"name": customer.name, "notes": customer.notes}
    error = None
    if request.method == "POST":
        values = {"name": request.form.get("name", ""), "notes": request.form.get("notes", "")}
        _, error = _save_customer(update_customer, customer, values["name"], values["notes"])
        if error is None:
            flash("客户信息已保存。", "success")
            return redirect(url_for("main.customers"))
    return render_template("customer_form.html", customer=customer, values=values, error=error)


@main_bp.post("/customers/<int:customer_id>/archive")
def archive(customer_id: int):
    customer = _customer_or_404(customer_id)
    _, error = _save_customer(archive_customer, customer)
    if error:
        flash(error, "error")
    else:
        flash("客户已归档。", "success")
    return redirect(url_for("main.customers"))


@main_bp.post("/customers/<int:customer_id>/restore")
def restore(customer_id: int):
    customer = _customer_or_404(customer_id)
    _, error = _save_customer(restore_customer, customer)
    if error:
        flash(error, "error")
    else:
        flash("客户已恢复。", "success")
    return redirect(url_for("main.customers"))
