from flask import Flask, render_template, redirect, request, url_for, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_
import datetime
import uuid
import math
import werkzeug
from marshmallow import Schema, fields, validate, ValidationError

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///loan.db"
db = SQLAlchemy(app)

# The lab is behind a http proxy, so it's not aware of the fact that it should use https.
# We use ProxyFix to enable it: https://flask.palletsprojects.com/en/2.0.x/deploying/wsgi-standalone/#proxy-setups
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Used for any other security related needs by extensions or application, i.e. csrf token
app.config['SECRET_KEY'] = 'mysecretkey'

# Required for cookies set by Flask to work in the preview window that's integrated in the lab IDE
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = True

# Required to render urls with https when not in a request context. Urls within Udemy labs must use https
app.config['PREFERRED_URL_SCHEME'] = 'https'


@app.route("/")
def index():
    print('Received headers', request.headers)
    return render_template('index.html')


@app.route("/redirect/")
def redirect_to_index():
    return redirect(url_for('index'))


class Loan(db.Model):
    loan_id = db.Column(db.String(36), primary_key=True)
    principal_amount = db.Column(db.Integer)
    term_months = db.Column(db.Integer)
    collateral_brand = db.Column(db.String(50))
    collateral_model = db.Column(db.String(50))
    collateral_manufacturing_year = db.Column(db.Integer)
    customer_name = db.Column(db.String(50))
    customer_birth_date = db.Column(db.DateTime)
    customer_monthly_income = db.Column(db.Integer)
    customer_id_number = db.Column(db.String(50))
    created_by = db.Column(db.String(50))
    status = db.Column(db.String(50))
    loan_interest = db.Column(db.Integer)
    monthly_installment = db.Column(db.Integer)

    def to_dict(self):
        collateral = {}
        collateral['brand'] = self.collateral_brand
        collateral['model'] = self.collateral_model
        collateral['manufacturing_year'] = self.collateral_manufacturing_year

        customer = {}
        customer['id_number'] = self.customer_id_number
        customer['birth_date'] = datetime.datetime.strftime(self.customer_birth_date, '%Y-%m-%d')
        customer['monthly_income'] = self.customer_monthly_income
        customer['name'] = self.customer_name

        return {
            'loan_id': self.loan_id,
            'principal_amount': self.principal_amount,
            'term_months': self.term_months,
            'collateral': collateral,
            'customer': customer,
            'status': self.status,
            'interest': self.loan_interest,
            'monthly_installment': self.monthly_installment
        }


with app.app_context():
    db.create_all()


def save_loan_to_database(loan_request, partner_secret):
    parsed_birth_date = datetime.datetime.strptime(loan_request['customer']['birth_date'], '%Y-%m-%d')
    parsed_principal_amount = loan_request['principal_amount']
    parsed_term_months = loan_request['term_months']

    loan_interest = math.ceil(parsed_principal_amount * parsed_term_months * 0.01)
    monthly_installment = math.ceil((parsed_principal_amount + loan_interest) / parsed_term_months)

    loan = Loan(
        loan_id=str(uuid.uuid4()),
        principal_amount=parsed_principal_amount,
        term_months=parsed_term_months,
        collateral_brand=loan_request['collateral']['brand'],
        collateral_model=loan_request['collateral']['model'],
        collateral_manufacturing_year=loan_request['collateral']['manufacturing_year'],
        customer_id_number=loan_request['customer']['id_number'],
        customer_name=loan_request['customer']['name'],
        customer_monthly_income=loan_request['customer']['monthly_income'],
        customer_birth_date=parsed_birth_date,
        created_by=partner_secret,
        status='PENDING',
        loan_interest=loan_interest,
        monthly_installment=monthly_installment
    )

    db.session.add(loan)
    db.session.commit()

    return loan


class CollateralSchema(Schema):
    brand = fields.String(required=True, validate=validate.Length(max=50))
    model = fields.String(required=True, validate=validate.Length(max=50))
    manufacturing_year = fields.Integer(
        required=True, validate=validate.Range(min=2015, max=datetime.date.today().year)
    )


class LoanSchema(Schema):
    principal_amount = fields.Integer(
        required=True, validate=validate.Range(min=100, max=99999)
    )

    term_months = fields.Integer(
        required=True, validate=validate.OneOf([3, 6, 9, 12, 15, 18, 24])
    )

    collateral = fields.Nested(CollateralSchema, required=True)


loan_schema = LoanSchema()


@app.route('/api/loan', methods=['POST'])
def submit_loan():
    errors = loan_schema.validate(request.json)

    if errors:
        raise LoanBusinessException(
            error_message="Validation failed",
            status_code=400,
            detail=str(errors)
        )

    saved_loan = save_loan_to_database(
        request.json, request.headers['partner_secret']
    )

    return jsonify(
        customer_name=saved_loan.customer_name,
        loan_id=saved_loan.loan_id,
        status=saved_loan.status
    ), 201


@app.route('/api/loan', methods=['GET'])
def track_loan():
    filter_loan = Loan.query.filter(
        and_(Loan.loan_id == request.args['loan_id'], Loan.created_by == request.headers['partner_secret']))

    existing_loan = filter_loan.first()

    if existing_loan is None:
        raise LoanBusinessException(
            error_message="Loan does not exists",
            status_code=403,
            detail="Loan id " + request.args['loan_id'] + " does not exists or does not belong to you"
        )

    return jsonify(
        existing_loan.to_dict()
    ), 200


# ERROR HANDLERS

@app.errorhandler(werkzeug.exceptions.BadRequest)
@app.errorhandler(TypeError)
def handle_bad_request(ex):
    return jsonify(
        error_message='Bad request parameter / body. Please re-check submitted data.',
        detail=str(ex)
    ), 400


@app.errorhandler(Exception)
def handle_any_uncaught_exception(ex):
    return jsonify(
        error_message='Server cannot process request.',
        detail=str(ex)
    ), 500


class LoanBusinessException(Exception):
    def __init__(self, error_message, detail, status_code=None):
        self.error_message = error_message
        self.detail = detail

        if status_code is not None:
            self.status_code = status_code
        else:
            self.status_code = 500

    def to_dict(self):
        return {
            'error_message': self.error_message,
            'detail': self.detail
        }


@app.errorhandler(LoanBusinessException)
def handle_loan_business_exception(ex):
    return jsonify(
        error_message=ex.error_message,
        detail=ex.detail
    ), ex.status_code

# def validate_loan_for_business(loan_request):
#     # Age must between 18-70
#     parsed_birth_date = datetime.datetime.strptime(loan_request['customer']['birth_date'], '%Y-%m-%d')
#     today = datetime.date.today()
#     age = today.year - parsed_birth_date.year
#
#     if age < 18 or age > 70:
#         raise LoanBusinessException(
#             error_message="Age must between 18-70 years",
#             status_code=400,
#             detail="Submitted birth date is out of range, age is " + str(age) + " years"
#         )
#
#     # Loan principal amount between 100-99999
#     parsed_principal_amount = loan_request['principal_amount']
#
#     if parsed_principal_amount < 100 or parsed_principal_amount > 99999:
#         raise LoanBusinessException(
#             error_message="Loan principal amount must between 100-99999",
#             status_code=400,
#             detail="Submitted principal amount is out of range: " + str(parsed_principal_amount)
#         )
