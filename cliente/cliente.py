#!/usr/bin/env python3
# -*- coding: utf-8 -*-
####################################################################
# Camada Fisica da Computacao
# Projeto 3 - Protocolo de transmissao de arquivos por datagramas
#
# APLICACAO  C L I E N T E
#
# O que este programa faz:
#   1. HANDSHAKE: manda HELLO para ver se o servidor esta vivo e
#      recebe a lista de arquivos disponiveis (mostrada na tela);
#   2. ESCOLHA: o usuario escolhe 2 ou mais arquivos, um por vez,
#      e o servidor confirma cada escolha;
#   3. DOWNLOAD: recebe os arquivos de forma ALTERNADA (um pacote de
#      cada arquivo por rodada), confirmando cada pacote com ACK e
#      pedindo reenvio (NACK) se o checksum nao bater;
#   4. TECLADO durante o download:
#         P = pausar     R = retomar     A = abortar
#   5. ao final salva os arquivos em downloads/ e imprime o resumo
#      (tamanho e numero de pacotes de cada arquivo).
#
# Se os fios forem desconectados no meio, e so reconectar: o servidor
# fica reenviando o pacote pendente e a transmissao continua sozinha.
#
# O formato do datagrama esta descrito em datagrama.py.
####################################################################
 
import os
import sys
import time
 
from enlace import *
import datagrama as dg
 
try:
    import msvcrt                       # teclado sem bloquear (Windows)
except ImportError:
    msvcrt = None
 
# ==================================================================
# CONFIGURACAO
# ==================================================================
# Descubra a sua porta com:  python -m serial.tools.list_ports
serialName = "COM3"                      # Windows (esta e a porta deste PC)
 
PASTA_DOWNLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "downloads")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "log_cliente.txt")
 
TIMEOUT_RESPOSTA = 3.0    # espera por respostas de controle antes de reenviar
ESPERA_FINAL     = 5.0    # tempo de linha quieta antes de encerrar o download
 
 
# ==================================================================
# AUXILIARES
# ==================================================================
def pedir(p, tipo_pedido, tipo_resposta, id_arq=0, descricao=""):
    """Envia um pedido e espera a resposta certa, reenviando se demorar.
 
    E assim que o cliente descobre se o servidor esta vivo: se nao
    vier resposta dentro do timeout, avisa e tenta de novo.
    """
    while True:
        p.enviar(tipo_pedido, id_arq=id_arq)
        limite = time.time() + TIMEOUT_RESPOSTA
        while time.time() < limite:
            d = p.receber(timeout=0.2)
            if d is not None and d["ok"] and d["tipo"] == tipo_resposta:
                return d
        print("   ... servidor nao respondeu ({}), tentando de novo".format(
            descricao))
 
 
def tecla_apertada():
    """Devolve a tecla apertada (minuscula) ou None, sem bloquear."""
    if msvcrt is not None and msvcrt.kbhit():
        try:
            return msvcrt.getch().decode(errors="ignore").lower()
        except Exception:
            return None
    return None
 
 
# ==================================================================
# FASE 1 - HANDSHAKE E ESCOLHA
# ==================================================================
def handshake(p):
    print("\n[1] HANDSHAKE - verificando se o servidor esta vivo...")
    d = pedir(p, dg.HELLO, dg.LISTA, descricao="HELLO")
    print("    servidor vivo! Arquivos disponiveis:\n")
    print("    " + d["payload"].decode(errors="replace").replace("\n", "\n    "))
    return d
 
 
def escolher_arquivos(p):
    escolhidos = []
    print("\n[2] ESCOLHA DOS ARQUIVOS (minimo de 2)")
    while True:
        texto = input("\n    numero do arquivo desejado: ").strip()
        if not texto.isdigit() or int(texto) < 1:
            print("    ... digite o NUMERO do arquivo (ex: 1)")
            continue
        if int(texto) in escolhidos:
            print("    ... este arquivo ja foi escolhido, pegue outro")
            continue
 
        d = pedir(p, dg.ESCOLHA, dg.RESPOSTA, id_arq=int(texto),
                  descricao="ESCOLHA")
        print("    servidor: {}".format(d["payload"].decode(errors="replace")))
        if d["id"] == 0:                     # servidor recusou (id invalido)
            continue
        escolhidos.append(int(texto))
 
        if len(escolhidos) < 2:
            print("    (o projeto exige pelo menos 2 arquivos, escolha outro)")
            continue
 
        resposta = input("    adicionar outro arquivo? (s/n): ").strip().lower()
        if resposta.startswith("s"):
            handshake(p)                     # mostra a lista de novo
        else:
            return
 
 
# ==================================================================
# FASE 2 - DOWNLOAD
# ==================================================================
def pedir_inicio(p):
    print("\n[3] Pedindo o inicio da transmissao...")
    # O checksum e simples (soma dos bytes), entao um INFO muito bagunçado
    # ainda pode passar por ele. Se nao der para entender, pedimos de novo.
    while True:
        d = pedir(p, dg.INICIAR, dg.INFO, descricao="INICIAR")
        try:
            arquivos = {}
            for linha in d["payload"].decode().split("\n"):
                idx, nome, tamanho, npacotes = linha.split(";")
                arquivos[int(idx)] = {"nome": nome, "tamanho": int(tamanho),
                                      "npacotes": int(npacotes),
                                      "dados": bytearray(), "prox": 1}
            break
        except Exception:
            print("    ... a lista de metadados chegou corrompida, pedindo de novo")
 
    print("    entendido! O servidor vai transmitir simultaneamente:")
    for a in arquivos.values():
        print("      - {} ({} bytes em {} pacotes)".format(
            a["nome"], a["tamanho"], a["npacotes"]))
    return arquivos
 
 
def pausar(p):
    p.enviar(dg.PAUSA)
    print("\n    || TRANSMISSAO PAUSADA ||  (R = retomar, A = abortar)")
    while True:
        tecla = tecla_apertada()
        if tecla == "r":
            p.enviar(dg.RETOMA)
            print("    >> transmissao retomada!\n")
            return True
        if tecla == "a":
            p.enviar(dg.ABORTA)
            return False
        time.sleep(0.05)
 
 
def download(p, arquivos):
    print("\n[4] DOWNLOAD  (teclas:  P = pausar   R = retomar   A = abortar)\n")
    if msvcrt is None:
        print("    [aviso] teclado ao vivo so funciona no Windows (msvcrt)\n")
 
    ultimo_pacote = time.time()
    while any(a["prox"] <= a["npacotes"] for a in arquivos.values()):
 
        tecla = tecla_apertada()
        if tecla == "p":
            if not pausar(p):
                print("\n    XX TRANSMISSAO ABORTADA pelo usuario XX")
                return False
        elif tecla == "a":
            p.enviar(dg.ABORTA)
            print("\n    XX TRANSMISSAO ABORTADA pelo usuario XX")
            return False
 
        d = p.receber(timeout=0.3)
        if d is None:
            if time.time() - ultimo_pacote > 4.0:
                # Nada de util ha 4 s. Se o fio caiu no meio de um byte,
                # sobrou um "meio byte" que desalinha toda a leitura: e por
                # isso que limpamos a linha antes de continuar esperando.
                print("    ... aguardando o servidor (fio desconectado?), "
                      "limpando a linha")
                p.ressincronizar()
                ultimo_pacote = time.time()
            continue
 
        if d["tipo"] != dg.DADOS:
            continue
        ultimo_pacote = time.time()
        arq = arquivos.get(d["id"])
        if arq is None:
            continue
 
        if not d["ok"]:
            print("    [RX] pacote {} de {} chegou CORROMPIDO -> NACK "
                  "(pedindo reenvio)".format(d["num"], arq["nome"]))
            p.enviar(dg.NACK, id_arq=d["id"], num=d["num"])
        elif d["num"] == arq["prox"]:
            arq["dados"] += d["payload"]
            arq["prox"]  += 1
            p.enviar(dg.ACK, id_arq=d["id"], num=d["num"])
            print("    [RX] {}: pacote {}/{} ({} bytes) -> ACK enviado".format(
                arq["nome"], d["num"], d["total"], len(d["payload"])))
            if arq["prox"] > arq["npacotes"]:
                arq["fim"] = time.time()
                print("    >>> {} recebido por completo! <<<\n".format(arq["nome"]))
        elif d["num"] < arq["prox"]:
            # duplicata (nosso ACK se perdeu) - confirma de novo e descarta
            p.enviar(dg.ACK, id_arq=d["id"], num=d["num"])
            print("    [RX] pacote {} de {} duplicado -> ACK reenviado".format(
                d["num"], arq["nome"]))
        else:
            p.enviar(dg.NACK, id_arq=d["id"], num=arq["prox"])
 
    # Ja temos tudo, mas o ACK do ultimo pacote pode ter se perdido no
    # caminho - nesse caso o servidor reenvia o pacote e ficaria preso.
    # Ficamos escutando ate a linha ficar quieta por 5 segundos seguidos,
    # reconfirmando tudo que chegar atrasado.
    print("    (escutando mais um pouco para reconfirmar pacotes atrasados)")
    limite = time.time() + ESPERA_FINAL
    while time.time() < limite:
        d = p.receber(timeout=0.2)
        if d is not None and d["ok"] and d["tipo"] == dg.DADOS:
            p.enviar(dg.ACK, id_arq=d["id"], num=d["num"])
            print("    [RX] pacote {} reenviado pelo servidor -> ACK "
                  "reconfirmado".format(d["num"]))
            limite = time.time() + ESPERA_FINAL     # zera a contagem
 
    return True
 
 
def salvar_e_resumir(arquivos, inicio):
    # o tempo vai ate o ultimo pacote, sem contar a escuta final
    tempo_total = max(a.get("fim", inicio) for a in arquivos.values()) - inicio
 
    print("\n[5] Salvando os arquivos em downloads/ ...")
    os.makedirs(PASTA_DOWNLOADS, exist_ok=True)
    for a in arquivos.values():
        caminho = os.path.join(PASTA_DOWNLOADS, a["nome"])
        with open(caminho, "wb") as f:
            f.write(a["dados"])
        print("    salvo: {}".format(caminho))
 
    print("\n" + "=" * 70)
    print(" RESUMO DA TRANSMISSAO  ({:.1f} s)".format(tempo_total))
    print("   {:<25} {:>10} {:>10} {:>10}".format(
        "arquivo", "esperado", "recebido", "pacotes"))
    tudo_ok = True
    for a in arquivos.values():
        ok = len(a["dados"]) == a["tamanho"]
        tudo_ok = tudo_ok and ok
        print("   {:<25} {:>10} {:>10} {:>10}   {}".format(
            a["nome"], a["tamanho"], len(a["dados"]), a["npacotes"],
            "OK" if ok else "CORROMPIDO!"))
    print("=" * 70)
    if tudo_ok:
        print(" [OK] SUCESSO - todos os arquivos chegaram integros!")
    else:
        print(" [ERRO] algum arquivo nao chegou completo!")
    print("=" * 70)
 
 
# ==================================================================
# PROGRAMA PRINCIPAL
# ==================================================================
def main():
    com1   = None
    aberto = False
    try:
        print("=" * 70)
        print(" PROJETO 3 - CLIENTE DE DOWNLOAD POR DATAGRAMAS")
        print("=" * 70)
        print("Porta serial ......: {}".format(serialName))
        print("Log de transmissao : {}".format(LOG_PATH))
 
        com1 = enlace(serialName)
        com1.enable()
        aberto = True
        com1.fisica.flush()
        print("Comunicacao serial aberta.")
 
        p = dg.Protocolo(com1, log_path=LOG_PATH)
 
        handshake(p)
        escolher_arquivos(p)
        arquivos = pedir_inicio(p)
 
        inicio = time.time()
        if download(p, arquivos):
            salvar_e_resumir(arquivos, inicio)
 
    except KeyboardInterrupt:
        print("\n\nInterrompido pelo usuario.")
    except Exception as erro:
        print("\n[ERRO] {}: {}".format(type(erro).__name__, erro))
        if not aberto:
            print("Nao consegui abrir a porta {}. Veja as portas disponiveis "
                  "com:\n    python -m serial.tools.list_ports".format(serialName))
    finally:
        if aberto:
            print("-" * 70)
            print("Comunicacao encerrada")
            com1.disable()
 
 
if __name__ == "__main__":
    # permite trocar a porta sem editar o arquivo:  python cliente.py COM3
    if len(sys.argv) > 1:
        serialName = sys.argv[1]
    main()
 