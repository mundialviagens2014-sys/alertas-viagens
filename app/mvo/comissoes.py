"""Comissão de quem emite: soma das emissões do mês por emissor e o valor da comissão de cada um."""
import io
from dataclasses import dataclass, field
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Usuario
from ..models_mvo import ContaPrograma, Emissao

BASES = {"lucro": "sobre o lucro", "venda": "sobre a venda", "total": "sobre o valor total", "passagem": "por passagem"}
# como cada regra aparece na escolha da tela; em "por passagem" o número é um valor em R$ (não %)
OPCOES_BASE = {"lucro": "% do lucro", "venda": "% da venda (sem taxas)", "total": "% do valor total (com taxas)",
               "passagem": "R$ por passagem vendida"}


def descrever_regra(valor: float | None, base: str) -> str:
    if not valor:
        return ""
    numero = f"{valor:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    if base == "passagem":
        return "R$ " + f"{valor:.2f}".replace(".", ",") + " por passagem"
    return f"{numero}% {BASES.get(base, base)}"
MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def ler_mes(texto: str | None, hoje: date | None = None) -> tuple[int, int]:
    """'2026-10' -> (2026, 10). Vazio ou inválido: mês atual."""
    hoje = hoje or date.today()
    try:
        ano, mes = (int(p) for p in (texto or "").split("-")[:2])
        if 2000 <= ano <= 2100 and 1 <= mes <= 12:
            return ano, mes
    except ValueError:
        pass
    return hoje.year, hoje.month


def intervalo_do_mes(ano: int, mes: int) -> tuple[datetime, datetime]:
    inicio = datetime.combine(date(ano, mes, 1), time.min)
    fim = datetime.combine(date(ano + (mes == 12), mes % 12 + 1, 1), time.min)
    return inicio, fim


def nome_do_mes(ano: int, mes: int) -> str:
    return f"{MESES[mes]} de {ano}"


def mes_vizinho(ano: int, mes: int, passo: int) -> str:
    indice = ano * 12 + (mes - 1) + passo
    return f"{indice // 12:04d}-{indice % 12 + 1:02d}"


@dataclass
class LinhaComissao:
    chave: str
    emissor_id: int | None
    emissor_nome: str
    emissoes: list[Emissao] = field(default_factory=list)
    pct: float | None = None
    base: str = "lucro"

    @property
    def quantidade(self) -> int:
        return len(self.emissoes)

    @property
    def passageiros(self) -> int:
        return sum(e.qtd_passageiros or 0 for e in self.emissoes)

    @property
    def pontos(self) -> int:
        return sum(e.pontos or 0 for e in self.emissoes if e.forma == "programa_proprio")

    @property
    def vendido(self) -> float:
        return round(sum(e.preco_venda or 0 for e in self.emissoes), 2)

    @property
    def custo(self) -> float:
        return round(sum(e.custo or 0 for e in self.emissoes), 2)

    @property
    def lucro(self) -> float:
        return round(sum(e.lucro or 0 for e in self.emissoes if e.lucro is not None), 2)

    @property
    def sem_venda(self) -> int:
        return sum(1 for e in self.emissoes if e.preco_venda is None)

    @property
    def total(self) -> float:
        """Valor todo cobrado do cliente: passagem + taxas."""
        return round(sum(e.total or 0 for e in self.emissoes), 2)

    @property
    def valor_base(self) -> float:
        return {"venda": self.vendido, "total": self.total, "passagem": self.passageiros}.get(self.base, self.lucro)

    @property
    def regra(self) -> str:
        return descrever_regra(self.pct, self.base)

    @property
    def comissao(self) -> float | None:
        if not self.pct:
            return None
        if self.base == "passagem":   # valor fixo em R$ para cada passagem (cada passageiro emitido)
            return round(self.passageiros * self.pct, 2)
        return round(max(self.valor_base, 0) * self.pct / 100, 2)


def comissoes_do_mes(db: Session, ano: int, mes: int, emissor_id: int | None = None) -> list[LinhaComissao]:
    inicio, fim = intervalo_do_mes(ano, mes)
    consulta = (
        select(Emissao).where(Emissao.status == "ativa", Emissao.data_emissao >= inicio, Emissao.data_emissao < fim)
        .options(selectinload(Emissao.conta).selectinload(ContaPrograma.colaborador), selectinload(Emissao.passageiros))
        .order_by(Emissao.data_emissao, Emissao.id)
    )
    if emissor_id:
        consulta = consulta.where(Emissao.emissor_id == emissor_id)
    usuarios = {u.id: u for u in db.scalars(select(Usuario))}
    linhas: dict[str, LinhaComissao] = {}
    for e in db.scalars(consulta):
        if e.emissor_id and e.emissor_id in usuarios:
            chave, nome = f"u{e.emissor_id}", usuarios[e.emissor_id].nome
        elif e.emissor_nome:
            chave, nome = "n" + e.emissor_nome.strip().lower(), e.emissor_nome.strip()
        else:
            chave, nome = "-", "Sem emissor informado"
        linha = linhas.get(chave)
        if linha is None:
            u = usuarios.get(e.emissor_id) if e.emissor_id else None
            linha = LinhaComissao(chave, u.id if u else None, nome, pct=u.comissao_pct if u else None,
                                  base=(u.comissao_base if u and u.comissao_base in BASES else "lucro"))
            linhas[chave] = linha
        linha.emissoes.append(e)
    return sorted(linhas.values(), key=lambda l: (l.chave == "-", -l.vendido, l.emissor_nome))


def planilha(linhas: list[LinhaComissao], ano: int, mes: int, com_custos: bool = True) -> bytes:
    """Planilha .xlsx com o resumo por emissor e a lista das emissões do mês."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    resumo = wb.active
    resumo.title = "Comissões"
    cab_resumo = ["Emissor", "Emissões", "Passageiros", "Pontos", "Vendido (R$)"]
    if com_custos:
        cab_resumo += ["Custo (R$)", "Lucro (R$)", "Regra", "% ou R$ por passagem", "Comissão (R$)"]
    resumo.append([f"Comissões de {nome_do_mes(ano, mes)}"])
    resumo["A1"].font = Font(bold=True, size=13)
    resumo.append(cab_resumo)
    for l in linhas:
        linha = [l.emissor_nome, l.quantidade, l.passageiros, l.pontos, l.vendido]
        if com_custos:
            linha += [l.custo, l.lucro, l.regra or "sem regra", l.pct or 0, l.comissao or 0]
        resumo.append(linha)

    detalhe = wb.create_sheet("Emissões")
    cab = ["Data", "Emissor", "Localizador", "Trecho", "Cliente", "Programa", "Conta", "Passageiros", "Pontos",
           "Venda (R$)", "Taxas (R$)"]
    if com_custos:
        cab += ["Custo (R$)", "Lucro (R$)"]
    detalhe.append(cab)
    for l in linhas:
        for e in l.emissoes:
            conta = f"{e.conta.colaborador.nome}" if e.conta else (e.forma or "")
            linha = [e.data_emissao.strftime("%d/%m/%Y"), l.emissor_nome, e.localizador or "", e.rota, e.cliente_nome or "",
                     e.programa or "", conta, e.qtd_passageiros, e.pontos or 0, e.preco_venda or 0,
                     round((e.taxa_embarque or 0) + (e.taxa_bagagem or 0), 2)]
            if com_custos:
                linha += [e.custo or 0, e.lucro if e.lucro is not None else ""]
            detalhe.append(linha)

    for ws, linha_cab in ((resumo, 2), (detalhe, 1)):
        for c in ws[linha_cab]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1B468F")
        for coluna in ws.columns:
            largura = max(len(str(c.value or "")) for c in coluna[linha_cab - 1:]) + 2
            ws.column_dimensions[coluna[0].column_letter].width = max(10, min(40, largura))
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()
