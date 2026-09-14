#!/usr/bin/env python3
# -*- coding: utf-8 -*-
####################################################################
# Camada Fisica da Computacao
# Projeto 3 - Protocolo de transmissao de arquivos por datagramas
#
# APLICACAO  S E R V I D O R
#
# O que este programa faz:
#   1. espera o HELLO do cliente e responde a lista de arquivos
#      da pasta "arquivos/";
#   2. registra as escolhas do cliente (2 ou mais arquivos);
#   3. quando o cliente manda INICIAR, envia os metadados (INFO) e
#      comeca a transmissao ALTERNADA (round-robin): um pacote de
#      cada arquivo, sempre esperando o ACK antes do proximo;
#   4. reenvia o pacote em caso de NACK (checksum errado) ou de
#      timeout sem resposta (fio desconectado) - a transmissao
#      retoma sozinha quando os fios voltam;
#   5. atende PAUSA / RETOMA / ABORTA enviados pelo cliente;
#   6. ao final imprime o resumo e volta a esperar um novo cliente.
#
# O formato do datagrama esta descrito em datagrama.py.
####################################################################
 
import os
import sys
import time
 
from enlace import *
import datagrama as dg
 
# ==================================================================
# CONFIGURACAO
# ==================================================================
# Descubra a sua porta com:  python -m serial.tools.list_ports
serialName = "/dev/tty.usbmodem11301"                     # Windows (troque pela sua!)
 
 
PASTA_ARQUIVOS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "arquivos")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "log_servidor.txt")
 
TIMEOUT_ACK = 2.0     # segundos esperando o ACK antes de reenviar o pacote
MAX_TENTATIVAS = 30   # reenvios sem resposta antes de desistir do cliente
                      # (30 x 2 s = 1 minuto de fio desconectado)
 
# ------------------------------------------------------------------
# Projeto 4 - simulacao de erros (hard coded), para gerar os 4 tipos
# de log pedidos no enunciado. Deixe as duas em False para uma
# transmissao normal, sem nenhuma intercorrencia.
# ------------------------------------------------------------------
SIMULAR_ERRO_ORDEM = False   # manda o pacote 2 antes do 1, de proposito
SIMULAR_ERRO_CRC   = False   # corrompe 1 byte do primeiro pacote, de proposito
 
 
# ==================================================================
# AUXILIARES
# ==================================================================
def listar_arquivos():
    """Nomes dos arquivos da pasta 'arquivos/', em ordem alfabetica."""
    return sorted(a for a in os.listdir(PASTA_ARQUIVOS)
                  if os.path.isfile(os.path.join(PASTA_ARQUIVOS, a)))
 
 
def enviar_lista(p, nomes):
    texto = "\n".join("{} - {}".format(i + 1, n) for i, n in enumerate(nomes))
    p.enviar(dg.LISTA, payload=texto.encode()[:dg.MAX_PAYLOAD])
    print("[TX] LISTA enviada ({} arquivos disponiveis)".format(len(nomes)))
 
 
def fragmentar(dados):
    """Divide os bytes do arquivo em pedacos de ate 100 bytes."""
    return [dados[i:i + dg.MAX_PAYLOAD]
            for i in range(0, len(dados), dg.MAX_PAYLOAD)] or [b""]
 
 
# ==================================================================
# FASE 1 - HANDSHAKE E ESCOLHA DOS ARQUIVOS
# ==================================================================
def atender_escolhas(p):
    """Atende HELLO/ESCOLHA ate o cliente mandar INICIAR.
 
    Devolve a lista de ids escolhidos (na ordem), ou None se nada rolou.
    """
    nomes      = listar_arquivos()
    escolhidos = []
 
    
    print(" Aguardando cliente... (arquivos disponiveis: {})".format(", ".join(nomes)))
    
 
    while True:
        d = p.receber(timeout=1.0)
        if d is None or not d["ok"]:
            continue
 
        if d["tipo"] == dg.HELLO:
            print("\n[RX] cliente esta vivo e pediu a lista de arquivos")
            nomes = listar_arquivos()
            enviar_lista(p, nomes)
 
        elif d["tipo"] == dg.ESCOLHA:
            idx = d["id"]
            if not 1 <= idx <= len(nomes):
                print("[RX] ESCOLHA invalida (id {})".format(idx))
                p.enviar(dg.RESPOSTA, id_arq=0,
                         payload=b"arquivo inexistente, escolha de novo")
                continue
            if idx not in escolhidos:
                escolhidos.append(idx)
            nomes_escolhidos = " e ".join(nomes[i - 1] for i in escolhidos)
            texto = "arquivo(s) {} escolhido(s), deseja adicionar outro arquivo?".format(
                nomes_escolhidos)
            print("[RX] ESCOLHA do arquivo {} ({})".format(idx, nomes[idx - 1]))
            print("[TX] RESPOSTA: {}".format(texto))
            p.enviar(dg.RESPOSTA, id_arq=idx,
                     payload=texto.encode()[:dg.MAX_PAYLOAD])
 
        elif d["tipo"] == dg.INICIAR and escolhidos:
            print("\n[RX] INICIAR - entendido. Vou iniciar a transmissao "
                  "simultanea dos arquivos escolhidos!")
            return nomes, escolhidos
 
 
# ==================================================================
# FASE 2 - TRANSMISSAO ALTERNADA COM ACK PACOTE A PACOTE
# ==================================================================
def transmitir(p, nomes, escolhidos):
    # carrega e fragmenta os arquivos escolhidos
    arquivos = {}
    for idx in escolhidos:
        nome  = nomes[idx - 1]
        with open(os.path.join(PASTA_ARQUIVOS, nome), "rb") as f:
            dados = f.read()
        arquivos[idx] = {"nome": nome, "tamanho": len(dados),
                         "pacotes": fragmentar(dados), "prox": 1}
 
    # INFO: "id;nome;tamanho;numero_de_pacotes" de cada arquivo
    info = "\n".join("{};{};{};{}".format(i, a["nome"], a["tamanho"],
                                          len(a["pacotes"]))
                     for i, a in arquivos.items())
    p.enviar(dg.INFO, payload=info.encode()[:dg.MAX_PAYLOAD])
    print("[TX] INFO com os metadados enviada. Comecando a transmissao!\n")
 
    ativos = list(arquivos.keys())      # arquivos ainda em transmissao
    vez    = 0                          # de quem e a vez (round-robin)
    inicio = time.time()
 
    # controla se ja simulamos cada erro uma vez (so a 1a oportunidade)
    simulado = {"ordem": False, "crc": False}
 
    while ativos:
        idx   = ativos[vez % len(ativos)]
        arq   = arquivos[idx]
        total = len(arq["pacotes"])
 
        # pend_num/pend_payload = o pacote que vamos mandar AGORA. Comeca
        # sendo o proximo esperado (arq["prox"]), mas pode ser trocado
        # pela simulacao de erro de ordem, ou depois por causa de um NACK.
        pend_num     = arq["prox"]
        pend_payload = arq["pacotes"][pend_num - 1]
        corromper    = False
 
        if SIMULAR_ERRO_ORDEM and not simulado["ordem"] and pend_num == 1 and total >= 2:
            print("     [SIMULACAO] erro de ORDEM proposital: mandando o "
                  "pacote 2 antes do 1!")
            pend_num     = 2
            pend_payload = arq["pacotes"][1]
            simulado["ordem"] = True
 
        if SIMULAR_ERRO_CRC and not simulado["crc"] and pend_num == 1:
            print("     [SIMULACAO] erro de CRC proposital no pacote 1!")
            corromper = True
            simulado["crc"] = True
 
        p.enviar(dg.DADOS, id_arq=idx, num=pend_num, total=total,
                 payload=pend_payload, corromper=corromper)
        print("[TX] DADOS {}: pacote {}/{} ({} bytes)".format(
            arq["nome"], pend_num, total, len(pend_payload)))
 
        # espera a reacao do cliente para ESTE pacote
        tentativas = 0
        while True:
            d = p.receber(timeout=TIMEOUT_ACK)
 
            if d is None:
                tentativas += 1
                if tentativas > MAX_TENTATIVAS:
                    print("     [erro] o cliente sumiu por mais de {:.0f} s. "
                          "Desistindo desta transmissao.".format(
                              MAX_TENTATIVAS * TIMEOUT_ACK))
                    return
                print("     [timeout {}] sem resposta do cliente "
                      "(fio desconectado?), reenviando pacote {}...".format(
                          tentativas, pend_num))
                if tentativas % 3 == 0:
                    # A cada 3 tentativas sem resposta, limpa a linha: se o
                    # fio caiu no meio de um byte, sobrou um "meio byte" que
                    # desalinha toda a leitura ate ser descartado.
                    print("     [limpando a linha para ressincronizar]")
                    p.ressincronizar()
                p.enviar(dg.DADOS, id_arq=idx, num=pend_num, total=total,
                         payload=pend_payload)
                continue
            if not d["ok"]:
                continue                       # mensagem corrompida, ignora
 
            if d["tipo"] == dg.ACK and d["id"] == idx and d["num"] == pend_num:
                print("     [RX] ACK do pacote {}/{} de {}".format(
                    pend_num, total, arq["nome"]))
                if pend_num == arq["prox"]:
                    # so avanca de verdade se o pacote confirmado era
                    # mesmo o que faltava (nao um pacote "extra" mandado
                    # so pela simulacao de erro de ordem)
                    arq["prox"] += 1
                if arq["prox"] > total:
                    print("{} transmitido por completo! \n".format(
                        arq["nome"]))
                    posicao = ativos.index(idx)
                    ativos.remove(idx)
                    vez = posicao              # mantem a alternancia
                else:
                    vez += 1                   # proximo arquivo da rodada
                break
 
            elif d["tipo"] == dg.NACK:
                # O cliente diz EXATAMENTE qual pacote quer em d["num"]
                # (pode ser por CRC errado OU por ordem errada - nos dois
                # casos a correcao e a mesma: mandar o pacote pedido).
                pend_num     = d["num"] if d["num"] else pend_num
                pend_payload = arq["pacotes"][pend_num - 1]
                print("     [RX] NACK - cliente pediu o pacote {}, "
                      "reenviando...".format(pend_num))
                p.enviar(dg.DADOS, id_arq=idx, num=pend_num, total=total,
                         payload=pend_payload)
 
            elif d["tipo"] == dg.PAUSA:
                print("\n     [RX] PAUSA - transmissao pausada pelo cliente. "
                      "Aguardando RETOMA ou ABORTA...")
                while True:
                    e = p.receber(timeout=1.0)
                    if e is None or not e["ok"]:
                        continue
                    if e["tipo"] == dg.RETOMA:
                        print("     [RX] RETOMA - retomando a transmissao!\n")
                        p.enviar(dg.DADOS, id_arq=idx, num=pend_num, total=total,
                                 payload=pend_payload)
                        break
                    if e["tipo"] == dg.ABORTA:
                        print("     [RX] ABORTA - transmissao abortada pelo cliente!")
                        return
                continue
 
            elif d["tipo"] == dg.ABORTA:
                print("\n     [RX] ABORTA - transmissao abortada pelo cliente!")
                return
 
            elif d["tipo"] == dg.INICIAR:
                # o cliente nao recebeu a INFO; reenvia e recomeca o pacote
                p.enviar(dg.INFO, payload=info.encode()[:dg.MAX_PAYLOAD])
                p.enviar(dg.DADOS, id_arq=idx, num=pend_num, total=total,
                         payload=pend_payload)
 
            # ACK duplicado ou qualquer outra coisa: so ignora
 
    # ------------------ resumo ------------------
    
    print(" TRANSMISSAO CONCLUIDA em {:.1f} s".format(time.time() - inicio))
    for idx in escolhidos:
        a = arquivos[idx]
        print("   {:<25} {:>8} bytes   {:>5} pacotes".format(
            a["nome"], a["tamanho"], len(a["pacotes"])))
    
 
 
# ==================================================================
# PROGRAMA PRINCIPAL
# ==================================================================
def main():
    com1   = None
    aberto = False
    try:
        print(" PROJETO 3 - SERVIDOR DE ARQUIVOS POR DATAGRAMAS")
        print("Porta serial ......: {}".format(serialName))
        print("Pasta de arquivos .: {}".format(PASTA_ARQUIVOS))
        print("Log de transmissao : {}".format(LOG_PATH))
 
        com1 = enlace(serialName)
        com1.enable()
        aberto = True
        com1.fisica.flush()
        print("Comunicacao serial aberta.")
 
        p = dg.Protocolo(com1, log_path=LOG_PATH)
 
        while True:                       # atende um cliente atras do outro
            nomes, escolhidos = atender_escolhas(p)
            transmitir(p, nomes, escolhidos)
 
    except KeyboardInterrupt:
        print("\n\nServidor encerrado pelo usuario.")
    except Exception as erro:
        print("\n[ERRO] {}: {}".format(type(erro).__name__, erro))
        if not aberto:
            print("Nao consegui abrir a porta {}. Veja as portas disponiveis "
                  "com:\n    python -m serial.tools.list_ports".format(serialName))
    finally:
        if aberto:
            print("Comunicacao encerrada")
            com1.disable()
 
 
if __name__ == "__main__":
    # permite trocar a porta sem editar o arquivo:  python servidor.py COM3
    if len(sys.argv) > 1:
        serialName = sys.argv[1]
    main()
 