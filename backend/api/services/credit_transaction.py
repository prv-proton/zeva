from datetime import date, datetime
from decimal import Decimal
from django.db.models import Case, Count, Sum, Value, F, Q, When, Max, FloatField, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.db import transaction
from rest_framework.serializers import ValidationError

from api.models.credit_class import CreditClass
from api.models.credit_transaction import CreditTransaction
from api.models.credit_transaction_type import CreditTransactionType
from api.models.credit_transfer_credit_transaction import \
    CreditTransferCreditTransaction
from api.models.sales_submission_credit_transaction import \
    SalesSubmissionCreditTransaction
from api.models.record_of_sale import RecordOfSale
from api.models.vehicle import Vehicle
from api.models.weight_class import WeightClass
from api.models.organization_deficits import OrganizationDeficits
from api.models.model_year_report import ModelYearReport
from api.models.model_year_report_statuses import ModelYearReportStatuses
from api.models.sales_submission import SalesSubmission

from api.services.credit_transfer import aggregate_credit_transfer_details


def aggregate_credit_balance_details(organization):
    balance_credits = Coalesce(Sum('total_value', filter=Q(
        credit_to=organization
    )), Value(0), output_field=FloatField())

    balance_debits = Coalesce(Sum('total_value', filter=Q(
        debit_from=organization
    )), Value(0), output_field=FloatField())

    balance = CreditTransaction.objects.filter(
        Q(credit_to=organization) | Q(debit_from=organization)
    ).values(
        'model_year_id', 'credit_class_id', 'weight_class_id'
    ).annotate(
        credit=balance_credits,
        debit=balance_debits,
        total_value=ExpressionWrapper(F('credit') - F('debit'), output_field=FloatField())
    ).order_by('model_year_id', 'credit_class_id', 'weight_class_id')

    return balance


def adjust_deficits(organization):
    """
    the following code automatically reduces their awarded credits
    if they have any deficits in their account
    only reduce the deficits relating to the credit class
    they selected to reduce first from the latest report
    """
    model_year_report = ModelYearReport.objects.filter(
        organization_id=organization.id,
        validation_status=ModelYearReportStatuses.ASSESSED
    ).order_by('-model_year__name').first()

    if not model_year_report:
        return False

    selected_credit_class = CreditClass.objects.filter(
        credit_class=model_year_report.credit_reduction_selection
    ).first()

    reduction_type = CreditTransactionType.objects.get(
        transaction_type="Reduction"
    )

    balances = aggregate_credit_balance_details(organization)
    balances = balances.filter(
        credit_class_id=selected_credit_class.id
    )

    credit_transaction = None
    total_current_reduction = 0

    for balance in balances:
        remaining_balance = Decimal(balance.get('total_value'))

        if remaining_balance <= 0:
            continue

        amount_to_reduce = 0
        credit_transaction = None

        deficits = OrganizationDeficits.objects.filter(
            organization_id=organization.id,
            credit_class_id=selected_credit_class.id
        ).order_by('model_year__name')

        for deficit in deficits:
            if remaining_balance > 0:
                if remaining_balance > deficit.credit_value:
                    amount_to_reduce = deficit.credit_value
                    remaining_balance -= deficit.credit_value
                else:
                    amount_to_reduce = remaining_balance
                    remaining_balance = 0

                credit_transaction = CreditTransaction.objects.create(
                    create_user="SYSTEM",
                    credit_class_id=selected_credit_class.id,
                    debit_from_id=organization.id,
                    model_year_id=balance.get('model_year_id'),
                    number_of_credits=1,
                    credit_value=amount_to_reduce,
                    transaction_type=reduction_type,
                    total_value=amount_to_reduce,
                    update_user="SYSTEM",
                    weight_class_id=balance.get('weight_class_id')
                )

                deficit.credit_value -= amount_to_reduce
                deficit.save()
                total_current_reduction += amount_to_reduce

    return True


def award_credits(submission):
    part_of_model_year_report = SalesSubmission.objects.filter(
        id=submission.id
    ).values_list('part_of_model_year_report', flat=True).first()
  
    records = RecordOfSale.objects.filter(
        submission_id=submission.id,
        validation_status="VALIDATED",
    ).values('vehicle_id').annotate(total=Count('id')).order_by('vehicle_id')

    weight_class = WeightClass.objects.get(weight_class_code="LDV")

    for record in records:
        current_year = datetime.now().year
        vehicle = Vehicle.objects.get(id=record.get('vehicle_id'))
        number_of_credits = record.get('total')
        credit_value = vehicle.get_credit_value()
        total_value = number_of_credits * credit_value
        credit_class = vehicle.get_credit_class()

        if credit_class in ['A', 'B']:
            vehicle_credit_class = CreditClass.objects.get(
                credit_class=credit_class
            )
            
            credit_transaction = CreditTransaction.objects.create(
                create_user=submission.update_user,
                credit_class=vehicle_credit_class,
                credit_to=submission.organization,
                credit_value=credit_value,
                model_year=vehicle.model_year,
                number_of_credits=number_of_credits,
                total_value=total_value,
                transaction_type=CreditTransactionType.objects.get(
                    transaction_type="Validation"
                ),
                update_user=submission.update_user,
                weight_class=weight_class,
            )

            if part_of_model_year_report and current_year:
                month = datetime.now().month
                if month <= 9:
                    current_year = current_year - 1

                credit_transaction.transaction_timestamp=date(current_year, 9, 30,)
                credit_transaction.save()

            SalesSubmissionCreditTransaction.objects.create(
                sales_submission_id=submission.id,
                credit_transaction_id=credit_transaction.id
            )

            adjust_deficits(submission.organization)


def aggregate_transactions_by_submission(organization):
    balance_credits = Coalesce(Sum('total_value', filter=Q(
        credit_to=organization
    )), Value(0), output_field=FloatField())

    balance_debits = Coalesce(Sum('total_value', filter=Q(
        debit_from=organization
    )), Value(0), output_field=FloatField())

    detail_transaction_type = Case(
        When(transaction_type=CreditTransactionType.objects.get(
            transaction_type="Credit Adjustment Reduction"
        ), then=F(
            'credit_agreement_credit_transaction__credit_agreement__transaction_type'
        )),
        When(transaction_type=CreditTransactionType.objects.get(
            transaction_type="Credit Adjustment Validation"
        ), then=F(
            'credit_agreement_credit_transaction__credit_agreement__transaction_type'
        )),
        default=Value(None)
    )

    foreign_key = Case(
        When(transaction_type=CreditTransactionType.objects.get(
            transaction_type="Validation"
        ), then=F(
            'sales_submission_credit_transaction__sales_submission_id'
        )),
        When(transaction_type=CreditTransactionType.objects.get(
            transaction_type="Credit Transfer"
        ), then=F(
            'credit_transfer_credit_transaction__credit_transfer_id'
        )),
        When(transaction_type=CreditTransactionType.objects.get(
            transaction_type="Credit Adjustment Validation"
        ), then=F(
            'credit_agreement_credit_transaction__credit_agreement_id'
        )),
        When(transaction_type=CreditTransactionType.objects.get(
            transaction_type="Credit Adjustment Reduction"
        ), then=F(
            'credit_agreement_credit_transaction__credit_agreement_id'
        )),
        When(
            transaction_type=CreditTransactionType.objects.get(
                transaction_type="Reduction"
            ),
            then=F(
                'model_year_report_credit_transaction__model_year_report_id'
            )
        ),
        default=Value(None)
    )

    transactions = CreditTransaction.objects.filter(
        Q(credit_to=organization) | Q(debit_from=organization)
    ).values(
        'credit_class_id', 'transaction_type_id', 'model_year_id'
    ).annotate(
        credit=balance_credits,
        debit=balance_debits,
        foreign_key=foreign_key,
        total_value=ExpressionWrapper(F('credit') - F('debit'), output_field=FloatField()),
        transaction_timestamp=Max('transaction_timestamp'),
        detail_transaction_type=detail_transaction_type
    ).order_by(
        'credit_class_id', 'transaction_type_id'
    )

    return transactions


def calculate_insufficient_credits(org_id):
    issued_balances = aggregate_credit_balance_details(org_id)
    issued_balances_list = list(issued_balances)
    pending_balance = aggregate_credit_transfer_details(org_id)
    for index, balance in enumerate(issued_balances_list):
        pending = pending_balance.filter(
            model_year_id=balance['model_year_id'],
            credit_class_id=balance['credit_class_id'],
            weight_class_id=balance['weight_class_id']
            ).first()
        if pending:
            total_balance = balance['total_value'] + pending['credit_value']
            update_list = {
                "model_year_id": balance['model_year_id'],
                "credit_class_id": balance['credit_class_id'],
                "weight_class_id": balance['weight_class_id'],
                "credit": balance['credit'],
                "debit": balance['debit'],
                "total_value": total_balance
            }
            issued_balances_list[index] = update_list
    return issued_balances_list


@transaction.atomic
def validate_transfer(transfer):
    initiating_supplier = transfer.debit_from
    receiving_supplier = transfer.credit_to
    content = transfer.credit_transfer_content.all()
    supplier_totals = aggregate_credit_balance_details(initiating_supplier.id)
    credit_total = {}
    credit_total_no_years = {}
    added_transaction = {}
    weight_class = WeightClass.objects.get(weight_class_code='LDV')
    has_enough = True

    for each in content:
        found = False
        # aggregate by unique combinations of credit year/type
        credit_value = each.credit_value
        model_year = each.model_year.id
        credit_type = each.credit_class.id
        weight_type = each.weight_class.id
        # check if supplier has enough for this transfer
        for record in supplier_totals:
            if (
                    record['model_year_id'] == model_year and
                    record['credit_class_id'] == credit_type and
                    record['weight_class_id'] == weight_type
            ):
                found = True
                record['total_value'] -= float(credit_value)
                if record['total_value'] < 0:
                    has_enough = False
        if not found:
            has_enough = False
        if not has_enough:
            raise ValidationError('Supplier has insufficient credits to fulfil this transfer.')
        else:
            # add to each dictionary (one broken down by years and the other not)
            if credit_type not in credit_total_no_years:
                credit_total_no_years[credit_type] = credit_value
            else:
                credit_total_no_years[credit_type] += credit_value

            if model_year not in credit_total:
                credit_total[model_year] = {}

            if credit_type not in credit_total[model_year]:
                credit_total[model_year][credit_type] = credit_value
            else:
                credit_total[model_year][credit_type] += credit_value

    for year, v in credit_total.items():
        for credit_class, credit_value in v.items():
            # add record for each unique combination to credit transaction table
            added_transaction = CreditTransaction.objects.create(
                create_user=transfer.update_user,
                credit_class=CreditClass.objects.get(
                    id=credit_class
                ),
                debit_from=transfer.debit_from,
                credit_to=transfer.credit_to,
                model_year_id=year,
                number_of_credits=1,
                credit_value=credit_value,
                transaction_type=CreditTransactionType.objects.get(
                    transaction_type="Credit Transfer"
                ),
                total_value=1 * credit_value,
                update_user=transfer.update_user,
                weight_class=weight_class
            )

            CreditTransferCreditTransaction.objects.create(
                create_user=transfer.update_user,
                credit_transaction_id=added_transaction.id,
                credit_transfer_id=transfer.id,
                update_user=transfer.update_user,
            )

    for year, v in credit_total.items():
        for credit_class, credit_value in v.items():
            adjust_deficits(transfer.credit_to)


def get_map_of_credit_transactions(key_field, value_field):
    result = {}
    credit_transactions = CreditTransaction.objects.only(key_field, value_field)
    for credit_transaction in credit_transactions:
        result[getattr(credit_transaction, key_field)] = getattr(credit_transaction, value_field)
    return result
