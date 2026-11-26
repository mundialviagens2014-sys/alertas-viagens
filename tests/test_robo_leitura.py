"""Leitura das respostas JSON dos sites das companhias (robo/leitura.py), em formatos diferentes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "robo"))

from leitura import montar_reserva  # noqa: E402

from app.mvo.bilhetes import limpar_reserva  # noqa: E402


def test_formato_com_segmentos_e_designador():
    respostas = [{"data": {"journeys": [
        {"segments": [
            {"flightDesignator": {"carrierCode": "G3", "flightNumber": "2072"},
             "departure": {"station": "GIG", "localDateTime": "2026-11-14T21:35:00-03:00"},
             "arrival": {"station": "JPA", "localDateTime": "2026-11-15T00:25:00-03:00"}}]}],
        "passengers": [{"name": {"first": "Stefany", "last": "Costa"},
                        "tickets": [{"ticketNumber": "127-2100000000"}]}]}}]
    r = montar_reserva(respostas)
    voo = r["trechos"][0]["voos"][0]
    assert voo == {"numero": "G32072", "origem": "GIG", "destino": "JPA", "partida": "2026-11-14T21:35",
                   "chegada": "2026-11-15T00:25", "operado_por": None, "cabine": None}
    assert r["passageiros"] == [{"nome": "STEFANY COSTA", "bilhete": "1272100000000"}]


def test_formato_plano_com_conexao_e_volta():
    respostas = [
        {"itinerary": [
            {"departureAirport": "FOR", "departureDateTime": "2026-12-10T05:30:00", "arrivalAirport": "GRU",
             "arrivalDateTime": "2026-12-10T08:55:00", "marketingCarrier": "LA", "flightNumber": "3456",
             "cabinClass": "Economy"},
            {"departureAirport": "GRU", "departureDateTime": "2026-12-10T10:20:00", "arrivalAirport": "LIS",
             "arrivalDateTime": "2026-12-10T23:50:00", "marketingCarrier": "LA", "flightNumber": "8084"},
            {"departureAirport": "LIS", "departureDateTime": "2026-12-20T13:00:00", "arrivalAirport": "FOR",
             "arrivalDateTime": "2026-12-20T19:10:00", "marketingCarrier": "LA", "flightNumber": "8085"}]},
        # a mesma reserva repetida em outra resposta, com menos campos: não duplica
        {"segments": [{"origin": {"iataCode": "FOR"}, "destination": {"iataCode": "GRU"},
                       "departure": {"at": "2026-12-10T05:30"}}]},
        {"travelers": [{"firstName": "MARIA", "lastName": "EXEMPLO"}, {"firstName": "MARIA", "lastName": "EXEMPLO"}]},
    ]
    r = montar_reserva(respostas, origem_pedida="FOR")
    assert [len(t["voos"]) for t in r["trechos"]] == [2, 1]
    assert [t["tipo"] for t in r["trechos"]] == ["ida", "volta"]
    assert r["trechos"][0]["voos"][0]["numero"] == "LA3456" and r["trechos"][0]["voos"][0]["cabine"] == "Economy"
    assert [p["nome"] for p in r["passageiros"]] == ["MARIA EXEMPLO"]
    # passa pela limpeza do MVO sem perder nada
    limpo = limpar_reserva(r)
    assert limpo["trechos"][1]["voos"][0]["destino"] == "FOR"


def test_sem_voo_nao_inventa():
    assert montar_reserva([{"mensagem": "Reserva não encontrada"}, {"firstName": "Ana", "lastName": "Lima"}]) is None
    # nome com número (ex.: código de cliente) não vira passageiro
    r = montar_reserva([{"departure": {"code": "FOR", "dateTime": "2026-12-01T10:00"},
                         "arrival": {"code": "REC", "dateTime": "2026-12-01T11:00"},
                         "user": {"firstName": "abc123", "lastName": "x9"}}])
    assert r["passageiros"] == []
