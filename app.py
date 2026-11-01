
import csv
import io
import os
import secrets
import sqlite3
from datetime import date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, Response
)
from werkzeug.security import generate_password_hash, check_password_hash

from database import get_connection, initialize_database


app = Flask(__name__)

# Use an environment secret when available.
app.secret_key = os.environ.get(
    "SECRET_KEY",
    secrets.token_hex(32)
)

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

initialize_database()


# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access the dashboard.", "error")
            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped_view


@app.route("/setup", methods=["GET", "POST"])
def setup():
    connection = get_connection()

    try:
        user_count = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]
    finally:
        connection.close()

    if user_count > 0:
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or len(username) < 3:
            flash("Username must contain at least 3 characters.", "error")
            return render_template("login.html", setup_mode=True)

        if len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
            return render_template("login.html", setup_mode=True)

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("login.html", setup_mode=True)

        connection = get_connection()

        try:
            connection.execute(
                """
                INSERT INTO users (username, password_hash)
                VALUES (?, ?)
                """,
                (username, generate_password_hash(password))
            )

            connection.commit()

            flash(
                "Administrator account created. Please log in.",
                "success"
            )

        except sqlite3.IntegrityError:
            connection.rollback()
            flash("That username already exists.", "error")
            return render_template("login.html", setup_mode=True)

        finally:
            connection.close()

        return redirect(url_for("login"))

    return render_template("login.html", setup_mode=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    connection = get_connection()

    try:
        user_count = connection.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]
    finally:
        connection.close()

    if user_count == 0:
        return redirect(url_for("setup"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        connection = get_connection()

        try:
            user = connection.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            ).fetchone()
        finally:
            connection.close()

        if user and check_password_hash(
            user["password_hash"], password
        ):
            session.clear()
            session["user_id"] = user["user_id"]
            session["username"] = user["username"]

            flash("Welcome back!", "success")
            return redirect(url_for("home"))

        flash("Incorrect username or password.", "error")

    return render_template("login.html", setup_mode=False)


@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.route("/")
@login_required
def home():
    connection = get_connection()

    try:
        total_products = connection.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        total_units = connection.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM products"
        ).fetchone()[0]

        low_stock = connection.execute(
            """
            SELECT COUNT(*)
            FROM products
            WHERE quantity <= reorder_level
            """
        ).fetchone()[0]

        search_query = request.args.get("search", "").strip()
        search_term = search_query.replace(" ", "")

        products = connection.execute(
            """
            SELECT products.*, suppliers.name AS supplier_name
            FROM products
            LEFT JOIN suppliers
                ON products.supplier_id = suppliers.supplier_id
            WHERE REPLACE(products.sku, ' ', '') LIKE ?
               OR REPLACE(products.name, ' ', '') LIKE ?
            ORDER BY products.product_id DESC
            """,
            (f"%{search_term}%", f"%{search_term}%")
        ).fetchall()

        suppliers = connection.execute(
            """
            SELECT supplier_id, name
            FROM suppliers
            ORDER BY name
            """
        ).fetchall()

    finally:
        connection.close()

    return render_template(
        "index.html",
        total_products=total_products,
        total_units=total_units,
        low_stock=low_stock,
        products=products,
        suppliers=suppliers,
        search_query=search_query
    )


# --------------------------------------------------
# PRODUCT MANAGEMENT
# --------------------------------------------------

@app.route("/products/<int:product_id>/edit")
@login_required
def edit_product(product_id):
    connection = get_connection()

    try:
        product = connection.execute(
            "SELECT * FROM products WHERE product_id = ?",
            (product_id,)
        ).fetchone()
    finally:
        connection.close()

    if product is None:
        flash("Product not found.", "error")
        return redirect(url_for("home"))

    return render_template("edit_product.html", product=product)


@app.route("/products/add", methods=["POST"])
@login_required
def add_product():
    sku = request.form.get("sku", "").strip()
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    supplier_id_text = request.form.get("supplier_id", "").strip()
    price_text = request.form.get("price", "").strip()
    quantity_text = request.form.get("quantity", "").strip()
    reorder_text = request.form.get("reorder_level", "5").strip()

    if not sku or not name or not category:
        flash("SKU, product name, and category are required.", "error")
        return redirect(url_for("home"))

    try:
        price = float(price_text)
        quantity = int(quantity_text)
        reorder_level = int(reorder_text)
        supplier_id = int(supplier_id_text) if supplier_id_text else None

        if price < 0 or quantity < 0 or reorder_level < 0:
            raise ValueError

    except ValueError:
        flash("Enter valid non-negative product values.", "error")
        return redirect(url_for("home"))

    connection = get_connection()

    try:
        if supplier_id is not None:
            supplier = connection.execute(
                "SELECT supplier_id FROM suppliers WHERE supplier_id = ?",
                (supplier_id,)
            ).fetchone()

            if supplier is None:
                flash("Selected supplier does not exist.", "error")
                return redirect(url_for("home"))

        connection.execute(
            """
            INSERT INTO products
                (sku, name, category, price, quantity,
                 reorder_level, supplier_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sku, name, category, price, quantity,
                reorder_level, supplier_id
            )
        )

        connection.commit()
        flash("Product added successfully!", "success")

    except sqlite3.IntegrityError:
        connection.rollback()
        flash("That SKU already exists. Please use a unique SKU.", "error")

    except Exception:
        connection.rollback()
        app.logger.exception("Could not add product")
        flash("Could not add product.", "error")

    finally:
        connection.close()

    return redirect(url_for("home"))


@app.route("/products/<int:product_id>/update", methods=["POST"])
@login_required
def update_product(product_id):
    sku = request.form.get("sku", "").strip()
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()

    try:
        price = float(request.form.get("price", ""))
        quantity = int(request.form.get("quantity", ""))
        reorder_level = int(request.form.get("reorder_level", ""))

        if not sku or not name or not category:
            raise ValueError

        if price < 0 or quantity < 0 or reorder_level < 0:
            raise ValueError

    except ValueError:
        flash("Please enter valid product details.", "error")
        return redirect(url_for("edit_product", product_id=product_id))

    connection = get_connection()

    try:
        result = connection.execute(
            """
            UPDATE products
            SET sku = ?, name = ?, category = ?,
                price = ?, quantity = ?, reorder_level = ?
            WHERE product_id = ?
            """,
            (
                sku, name, category, price,
                quantity, reorder_level, product_id
            )
        )

        if result.rowcount == 0:
            flash("Product not found.", "error")
        else:
            connection.commit()
            flash("Product updated successfully!", "success")

    except sqlite3.IntegrityError:
        connection.rollback()
        flash("That SKU already exists. Please use a unique SKU.", "error")

    except Exception:
        connection.rollback()
        app.logger.exception("Could not update product")
        flash("Could not update product.", "error")

    finally:
        connection.close()

    return redirect(url_for("home"))


@app.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def delete_product(product_id):
    connection = get_connection()

    try:
        result = connection.execute(
            "DELETE FROM products WHERE product_id = ?",
            (product_id,)
        )

        if result.rowcount == 0:
            flash("Product not found.", "error")
        else:
            connection.commit()
            flash("Product deleted successfully!", "success")

    except Exception:
        connection.rollback()
        app.logger.exception("Could not delete product")
        flash(
            "Could not delete product. It may have related stock records.",
            "error"
        )

    finally:
        connection.close()

    return redirect(url_for("home"))


# --------------------------------------------------
# SUPPLIER MANAGEMENT
# --------------------------------------------------

@app.route("/suppliers")
@login_required
def suppliers():
    connection = get_connection()

    try:
        supplier_list = connection.execute(
            """
            SELECT *
            FROM suppliers
            ORDER BY supplier_id DESC
            """
        ).fetchall()
    finally:
        connection.close()

    return render_template("suppliers.html", suppliers=supplier_list)


@app.route("/suppliers/add", methods=["POST"])
@login_required
def add_supplier():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()

    if not name:
        flash("Supplier name is required.", "error")
        return redirect(url_for("suppliers"))

    connection = get_connection()

    try:
        connection.execute(
            """
            INSERT INTO suppliers (name, email, phone, address)
            VALUES (?, ?, ?, ?)
            """,
            (name, email, phone, address)
        )

        connection.commit()
        flash("Supplier added successfully!", "success")

    except Exception:
        connection.rollback()
        app.logger.exception("Could not add supplier")
        flash("Could not add supplier.", "error")

    finally:
        connection.close()

    return redirect(url_for("suppliers"))


@app.route("/suppliers/<int:supplier_id>/edit")
@login_required
def edit_supplier(supplier_id):
    connection = get_connection()

    try:
        supplier = connection.execute(
            "SELECT * FROM suppliers WHERE supplier_id = ?",
            (supplier_id,)
        ).fetchone()
    finally:
        connection.close()

    if supplier is None:
        flash("Supplier not found.", "error")
        return redirect(url_for("suppliers"))

    return render_template("edit_supplier.html", supplier=supplier)


@app.route("/suppliers/<int:supplier_id>/update", methods=["POST"])
@login_required
def update_supplier(supplier_id):
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()

    if not name:
        flash("Supplier name is required.", "error")
        return redirect(url_for("edit_supplier", supplier_id=supplier_id))

    connection = get_connection()

    try:
        result = connection.execute(
            """
            UPDATE suppliers
            SET name = ?, email = ?, phone = ?, address = ?
            WHERE supplier_id = ?
            """,
            (name, email, phone, address, supplier_id)
        )

        if result.rowcount == 0:
            flash("Supplier not found.", "error")
        else:
            connection.commit()
            flash("Supplier updated successfully!", "success")

    except Exception:
        connection.rollback()
        app.logger.exception("Could not update supplier")
        flash("Could not update supplier.", "error")

    finally:
        connection.close()

    return redirect(url_for("suppliers"))


@app.route("/suppliers/<int:supplier_id>/delete", methods=["POST"])
@login_required
def delete_supplier(supplier_id):
    connection = get_connection()

    try:
        result = connection.execute(
            "DELETE FROM suppliers WHERE supplier_id = ?",
            (supplier_id,)
        )

        if result.rowcount == 0:
            flash("Supplier not found.", "error")
        else:
            connection.commit()
            flash("Supplier deleted successfully!", "success")

    except Exception:
        connection.rollback()
        app.logger.exception("Could not delete supplier")
        flash("Could not delete supplier.", "error")

    finally:
        connection.close()

    return redirect(url_for("suppliers"))


# --------------------------------------------------
# STOCK IN / STOCK OUT
# --------------------------------------------------

@app.route("/stock", methods=["GET", "POST"])
@login_required
def stock_movement():
    connection = get_connection()

    try:
        if request.method == "POST":
            try:
                product_id = int(request.form.get("product_id", ""))
                quantity = int(request.form.get("quantity", ""))

                if quantity <= 0:
                    raise ValueError

            except ValueError:
                flash("Please enter a valid product and quantity.", "error")
                return redirect(url_for("stock_movement"))

            movement_type = request.form.get("movement_type", "").strip()
            notes = request.form.get("notes", "").strip()

            if movement_type not in ("IN", "OUT"):
                flash("Invalid stock movement type.", "error")
                return redirect(url_for("stock_movement"))

            product = connection.execute(
                "SELECT quantity FROM products WHERE product_id = ?",
                (product_id,)
            ).fetchone()

            if product is None:
                flash("Product not found.", "error")
                return redirect(url_for("stock_movement"))

            current_quantity = product["quantity"]

            if movement_type == "OUT" and quantity > current_quantity:
                flash("Insufficient stock available.", "error")
                return redirect(url_for("stock_movement"))

            new_quantity = (
                current_quantity + quantity
                if movement_type == "IN"
                else current_quantity - quantity
            )

            connection.execute(
                "UPDATE products SET quantity = ? WHERE product_id = ?",
                (new_quantity, product_id)
            )

            connection.execute(
                """
                INSERT INTO stock_movements
                    (product_id, movement_type, quantity, notes)
                VALUES (?, ?, ?, ?)
                """,
                (product_id, movement_type, quantity, notes)
            )

            connection.commit()
            flash("Stock movement recorded successfully!", "success")

            return redirect(url_for("stock_movement"))

        products = connection.execute(
            """
            SELECT product_id, sku, name, quantity
            FROM products
            ORDER BY name
            """
        ).fetchall()

        return render_template("stock.html", products=products)

    except Exception:
        connection.rollback()
        app.logger.exception("Stock movement failed")
        flash("Could not process stock movement.", "error")
        return redirect(url_for("stock_movement"))

    finally:
        connection.close()


# --------------------------------------------------
# DATE-WISE STOCK HISTORY
# --------------------------------------------------

@app.route("/stock-history")
@login_required
def stock_history():
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    try:
        if start_date:
            date.fromisoformat(start_date)

        if end_date:
            date.fromisoformat(end_date)

        if start_date and end_date and start_date > end_date:
            flash("Start date cannot be later than end date.", "error")
            start_date = ""
            end_date = ""

    except ValueError:
        flash("Please select valid dates.", "error")
        start_date = ""
        end_date = ""

    query = """
        SELECT
            stock_movements.movement_id,
            stock_movements.movement_type,
            stock_movements.quantity,
            stock_movements.notes,
            stock_movements.created_at,
            products.name,
            products.sku
        FROM stock_movements
        JOIN products
            ON stock_movements.product_id = products.product_id
        WHERE 1 = 1
    """

    parameters = []

    if start_date:
        query += " AND date(stock_movements.created_at) >= date(?)"
        parameters.append(start_date)

    if end_date:
        query += " AND date(stock_movements.created_at) <= date(?)"
        parameters.append(end_date)

    query += " ORDER BY stock_movements.movement_id DESC"

    connection = get_connection()

    try:
        movements = connection.execute(
            query, parameters
        ).fetchall()
    finally:
        connection.close()

    return render_template(
        "stock_history.html",
        movements=movements,
        start_date=start_date,
        end_date=end_date
    )


# --------------------------------------------------
# REPORTS AND ANALYTICS
# --------------------------------------------------

@app.route("/reports")
@login_required
def reports():
    connection = get_connection()

    try:
        total_value = connection.execute(
            """
            SELECT COALESCE(SUM(price * quantity), 0)
            FROM products
            """
        ).fetchone()[0]

        total_stock_in = connection.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM stock_movements
            WHERE movement_type = 'IN'
            """
        ).fetchone()[0]

        total_stock_out = connection.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM stock_movements
            WHERE movement_type = 'OUT'
            """
        ).fetchone()[0]

        low_stock_products = connection.execute(
            """
            SELECT sku, name, quantity, reorder_level
            FROM products
            WHERE quantity <= reorder_level
            ORDER BY quantity ASC
            """
        ).fetchall()

        stock_chart_data = connection.execute(
            """
            SELECT name, quantity
            FROM products
            ORDER BY quantity DESC
            """
        ).fetchall()

        category_chart_data = connection.execute(
            """
            SELECT category, SUM(quantity) AS total_quantity
            FROM products
            GROUP BY category
            ORDER BY total_quantity DESC
            """
        ).fetchall()

    finally:
        connection.close()

    return render_template(
        "reports.html",
        total_value=total_value,
        total_stock_in=total_stock_in,
        total_stock_out=total_stock_out,
        low_stock_products=low_stock_products,
        stock_chart_data=stock_chart_data,
        category_chart_data=category_chart_data
    )


# --------------------------------------------------
# EXPORT INVENTORY REPORTS
# --------------------------------------------------

def get_inventory_export_data():
    connection = get_connection()

    try:
        return connection.execute(
            """
            SELECT
                products.sku,
                products.name,
                products.category,
                products.price,
                products.quantity,
                products.reorder_level,
                suppliers.name AS supplier_name,
                (products.price * products.quantity) AS stock_value
            FROM products
            LEFT JOIN suppliers
                ON products.supplier_id = suppliers.supplier_id
            ORDER BY products.name
            """
        ).fetchall()
    finally:
        connection.close()


@app.route("/reports/export/csv")
@login_required
def export_csv():
    products = get_inventory_export_data()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "SKU", "Product Name", "Category", "Unit Price",
        "Quantity", "Reorder Level", "Supplier", "Stock Value"
    ])

    for product in products:
        writer.writerow([
            product["sku"],
            product["name"],
            product["category"],
            product["price"],
            product["quantity"],
            product["reorder_level"],
            product["supplier_name"] or "",
            product["stock_value"]
        ])

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=inventory_report.csv"
        }
    )


@app.route("/reports/export/excel")
@login_required
def export_excel():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

    except ImportError:
        flash(
            "Excel export requires openpyxl. Install it using "
            "pip install openpyxl.",
            "error"
        )
        return redirect(url_for("reports"))

    products = get_inventory_export_data()

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Inventory Report"

    headers = [
        "SKU", "Product Name", "Category", "Unit Price",
        "Quantity", "Reorder Level", "Supplier", "Stock Value"
    ]

    worksheet.append(headers)

    for product in products:
        worksheet.append([
            product["sku"],
            product["name"],
            product["category"],
            product["price"],
            product["quantity"],
            product["reorder_level"],
            product["supplier_name"] or "",
            product["stock_value"]
        ])

    # Format the heading row.
    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(
            fill_type="solid",
            fgColor="1E293B"
        )
        cell.alignment = Alignment(horizontal="center")

    # Adjust column widths.
    for column in worksheet.columns:
        column_letter = get_column_letter(column[0].column)
        maximum_length = max(
            len(str(cell.value or "")) for cell in column
        )
        worksheet.column_dimensions[column_letter].width = (
            maximum_length + 4
        )

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)

    return Response(
        output.getvalue(),
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                "attachment; filename=inventory_report.xlsx"
        }
    )


# --------------------------------------------------
# RUN APPLICATION
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
