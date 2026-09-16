"""Юнит-тесты клиента ЕИС на моках — реальный SOAP-сервис не вызывается."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from eis_client.client import EISClient
from eis_client.config import EISConfig
from eis_client.models import Purchase


def make_config(tmp_path) -> EISConfig:
    cert = tmp_path / "client.pem"
    key = tmp_path / "client.key"
    cert.write_text("dummy-cert")
    key.write_text("dummy-key")
    return EISConfig(
        wsdl_url="https://example.invalid/service?wsdl",
        operation_name="getDocsByOrgRegion",
        client_cert=str(cert),
        client_key=str(key),
        client_key_password=None,
        region_code="77",
        okpd2_codes=["41", "42", "43"],
        timeout=5,
    )


def test_get_purchases_by_okpd2_maps_soap_response(tmp_path):
    config = make_config(tmp_path)

    fake_item = SimpleNamespace(
        purchaseNumber="0173200001426000001",
        purchaseObjectInfo="Строительство школы",
        customerName="ГКУ Москвы Стройзаказ",
        OKPD2="41.20",
        regionCode="77",
        maxPrice=150_000_000.0,
        publishDate=None,
    )
    fake_response = SimpleNamespace(purchases=[fake_item])

    with patch.object(EISClient, "_build_soap_client") as build_client, patch.object(
        EISClient, "_build_session"
    ) as build_session:
        build_session.return_value = MagicMock()
        mock_service = MagicMock()
        mock_service.getDocsByOrgRegion.return_value = fake_response
        build_client.return_value = SimpleNamespace(service=mock_service)

        client = EISClient(config)
        result = client.get_purchases_by_okpd2()

    assert len(result) == 1
    purchase = result[0]
    assert isinstance(purchase, Purchase)
    assert purchase.purchase_number == "0173200001426000001"
    assert purchase.customer_name == "ГКУ Москвы Стройзаказ"
    assert purchase.max_price == 150_000_000.0

    mock_service.getDocsByOrgRegion.assert_called_once_with(
        regionCodes=["77"], okpd2Codes=["41", "42", "43"]
    )


def test_missing_operation_raises(tmp_path):
    from eis_client.exceptions import EISRequestError

    config = make_config(tmp_path)

    with patch.object(EISClient, "_build_soap_client") as build_client, patch.object(
        EISClient, "_build_session"
    ) as build_session:
        build_session.return_value = MagicMock()
        build_client.return_value = SimpleNamespace(service=SimpleNamespace())

        client = EISClient(config)
        try:
            client.get_purchases_by_okpd2()
            assert False, "ожидалась EISRequestError"
        except EISRequestError:
            pass
