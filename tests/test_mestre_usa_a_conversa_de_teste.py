"""
O que a IA Mestre lê ao diagnosticar.

Achado pelo Luiz usando (2026-08-18): ele conduziu uma conversa no simulador para
expor um comportamento, clicou em Diagnosticar, e o diagnóstico veio sobre OUTRA
coisa — "parecia que ela testava de novo por ela mesma".

Era `real_history if real_history else [simulador]`. Bastava existir UMA conversa
no banco para a conversa que ele acabou de conduzir ser descartada inteira — e é
justamente ela o sinal mais deliberado que existe. O comentário acima do bloco já
dizia "combina" e "complementa"; o código substituía.
"""

import inspect

import admin.ai_agent as aa


def _fonte():
    """
    Só o CÓDIGO. O comentário da função cita o padrão antigo (`real_history if
    real_history else`) justamente para explicar o bug — ler a fonte crua faria o
    teste acusar a própria documentação.
    """
    return "\n".join(
        l for l in inspect.getsource(aa.improve_prompt).splitlines()
        if not l.lstrip().startswith("#")
    )


def test_o_simulador_nao_e_plano_B():
    fonte = _fonte()

    assert "real_history + simulado" in fonte, \
        "o histórico do simulador voltou a ser descartado quando há conversa real"
    assert "real_history if real_history else" not in fonte, \
        "voltou o `else` que troca uma fonte pela outra em vez de somar"


def test_a_conversa_de_teste_entra_por_ULTIMO():
    """
    Ordem importa: o que o operador acabou de reproduzir é o mais recente e o que
    ele quer discutir. Enterrado no meio de 40 mensagens antigas, vira ruído.
    """
    fonte = _fonte()
    i_real = fonte.index("real_history + simulado")
    assert fonte[i_real:i_real + 40].startswith("real_history + simulado"), \
        "a ordem mudou — o teste precisa vir depois do histórico real"


def test_o_operador_consegue_saber_o_que_a_mestre_leu():
    """
    Sem log, um diagnóstico sobre a base errada é indistinguível de um diagnóstico
    ruim — foi exatamente o que aconteceu.
    """
    fonte = _fonte()

    assert "msgs reais" in fonte and "simulador" in fonte, \
        "falta o log dizendo quantas mensagens de cada fonte entraram"
