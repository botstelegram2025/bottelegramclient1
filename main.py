def _activate_user_subscription(session, db_user, payment_id: str):
    """Marca usuário como ativo por 30 dias e grava last_payment_id se disponível."""
    from datetime import datetime, timedelta
    expires = datetime.now() + timedelta(days=30)
    if hasattr(db_user, "is_trial"):
        db_user.is_trial = False
    if hasattr(db_user, "is_active"):
        db_user.is_active = True
    # Adiciona next_due_date para garantir atualização correta
    for field in [
        "subscription_expires_at", "subscription_until",
        "premium_until", "paid_until", "expires_at", "next_due_date"
    ]:
        if hasattr(db_user, field):
            setattr(db_user, field, expires)
    if hasattr(db_user, "last_payment_id"):
        db_user.last_payment_id = str(payment_id)
    session.commit()
    return expires