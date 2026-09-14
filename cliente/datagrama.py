#!/usr/bin/env python3
# -*- coding: utf-8 -*-
####################################################################
# Camada Fisica da Computacao
# Projeto 4 - CRC-16 e log de transmissao
#
# Este modulo define o DATAGRAMA e e usado igualmente pelo cliente
# e pelo servidor (os dois lados tem uma copia identica dele).
#
#   +----------------+---------------------+-----------+
#   |  HEAD 12 bytes |  PAYLOAD 0..100 B   |  EOP 4 B  |
#   +----------------+---------------------+-----------+
#
# HEAD (tamanho fixo de 12 bytes):
#   byte  0     : TIPO da mensagem (ver tabela abaixo)
#   byte  1     : ID do arquivo (1, 2, 3...) ou 0 quando nao se aplica
#   bytes 2-3   : numero do pacote (big-endian, comeca em 1)
#   bytes 4-5   : total de pacotes do arquivo (big-endian)
#   byte  6     : tamanho do payload (0 a 100)
#   bytes 7-8   : CRC-16 do payload (big-endian) <- Projeto 4
#   bytes 9-11  : reservado (zeros)
#
# TIPOS de mensagem:
#   1  HELLO    cliente -> servidor : "esta vivo? quais arquivos tem?"
#   2  LISTA    servidor -> cliente : nomes dos arquivos disponiveis
#   3  ESCOLHA  cliente -> servidor : escolhe o arquivo de ID = head[1]
#   4  RESPOSTA servidor -> cliente : texto de resposta da escolha
#   5  INICIAR  cliente -> servidor : "nao quero mais, pode transmitir"
#   6  INFO     servidor -> cliente : metadados dos arquivos escolhidos
#   10 DADOS    servidor -> cliente : um pacote de dados de um arquivo
#   11 ACK      cliente -> servidor : confirma o pacote (id, num)
#   12 NACK     cliente -> servidor : pacote corrompido/fora de ordem, reenvie
#   20 PAUSA    cliente -> servidor : pausa a transmissao
#   21 RETOMA   cliente -> servidor : retoma a transmissao
#   22 ABORTA   cliente -> servidor : aborta a transmissao
####################################################################
 
import time
import binascii
from datetime import datetime
 
HEAD_SIZE   = 12
EOP         = b"\xAA\xBB\xCC\xDD"
MAX_PAYLOAD = 100
 
# tipos de mensagem
HELLO, LISTA, ESCOLHA, RESPOSTA, INICIAR, INFO = 1, 2, 3, 4, 5, 6
DADOS, ACK, NACK = 10, 11, 12
PAUSA, RETOMA, ABORTA = 20, 21, 22
 
NOME_TIPO = {1: "HELLO", 2: "LISTA", 3: "ESCOLHA", 4: "RESPOSTA",
             5: "INICIAR", 6: "INFO", 10: "DADOS", 11: "ACK",
             12: "NACK", 20: "PAUSA", 21: "RETOMA", 22: "ABORTA"}
 
 
def crc16(payload):
    """CRC-16 (variante CCITT) do payload, usando a biblioteca padrao do
    Python - o enunciado permite usar uma biblioteca pronta em vez de
    implementar o algoritmo na mao."""
    return binascii.crc_hqx(payload, 0)
 
 
def montar(tipo, id_arq=0, num=0, total=0, payload=b""):
    """Monta um datagrama completo: HEAD + PAYLOAD + EOP."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload maior que {} bytes".format(MAX_PAYLOAD))
    crc = crc16(payload)
    head = bytes([tipo, id_arq]) \
        + num.to_bytes(2, "big") \
        + total.to_bytes(2, "big") \
        + bytes([len(payload)]) \
        + crc.to_bytes(2, "big") \
        + b"\x00\x00\x00"
    return head + payload + EOP
 
 
def extrair(buf):
    """Tenta extrair UM datagrama do inicio do buffer.
 
    Devolve (datagrama, resto_do_buffer). Se ainda nao ha um datagrama
    completo, devolve (None, buffer).
 
    Se os primeiros bytes forem lixo (fios reconectados, ruido do
    Arduino), o EOP nao vai bater na posicao esperada: descartamos
    1 byte e tentamos de novo, ate ressincronizar. E assim que a
    transmissao sobrevive a desconexao dos fios.
    """
    while len(buf) >= HEAD_SIZE + len(EOP):
        tam = buf[6]
        if tam > MAX_PAYLOAD:            # head impossivel -> e lixo
            buf = buf[1:]
            continue
        fim = HEAD_SIZE + tam + len(EOP)
        if len(buf) < fim:               # datagrama ainda chegando
            return None, buf
        if buf[HEAD_SIZE + tam:fim] != EOP:   # EOP errado -> lixo
            buf = buf[1:]
            continue
        payload = buf[HEAD_SIZE:HEAD_SIZE + tam]
        crc_recebido = int.from_bytes(buf[7:9], "big")
        datagrama = {
            "tipo":    buf[0],
            "id":      buf[1],
            "num":     int.from_bytes(buf[2:4], "big"),
            "total":   int.from_bytes(buf[4:6], "big"),
            "payload": payload,
            "crc":     crc_recebido,
            "ok":      crc16(payload) == crc_recebido,   # CRC-16 confere?
        }
        return datagrama, buf[fim:]
    return None, buf
 
 
class Protocolo(object):
    """Envia e recebe datagramas por cima da camada de enlace.
 
    Alem disso, registra um LOG (txt) de cada pacote enviado/recebido,
    conforme pedido no Projeto 4: uma linha por pacote, com instante,
    direcao, tipo, tamanho total e (para pacotes DADOS) numero do
    pacote, total de pacotes e CRC do payload.
    """
 
    def __init__(self, com, log_path=None):
        self.com    = com
        self.buffer = b""
        self.log_path = log_path
        if log_path:
            # comeca um log novo a cada execucao
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("instante / direcao / tipo / tamanho_total"
                        " / [pacote / total_pacotes / CRC-16 (so p/ DADOS)]\n")
 
    def _registrar_log(self, direcao, tipo, tamanho_total, num=0, total=0, crc=0):
        if not self.log_path:
            return
        agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S.%f")[:-3]
        linha = "{} / {} / {} ({}) / {}".format(
            agora, direcao, tipo, NOME_TIPO.get(tipo, "?"), tamanho_total)
        if tipo == DADOS:
            linha += " / pacote {} / total {} / CRC {:04X}".format(num, total, crc)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
 
    def enviar(self, tipo, id_arq=0, num=0, total=0, payload=b"", corromper=False):
        pacote = montar(tipo, id_arq, num, total, payload)
        crc = crc16(payload)
        if corromper and len(pacote) > HEAD_SIZE:
            # SIMULACAO de erro de transmissao (Projeto 4, hard coded):
            # o CRC no head continua sendo o do payload ORIGINAL (assim
            # como aconteceria numa corrupcao de verdade no fio, depois
            # que o CRC ja tinha sido calculado) - so o BYTE que sai na
            # linha e alterado.
            pacote = bytearray(pacote)
            pacote[HEAD_SIZE] ^= 0xFF
            pacote = bytes(pacote)
        self.com.sendData(pacote)
        self._registrar_log("envio", tipo, len(pacote), num=num, total=total, crc=crc)
        limite = time.time() + 5.0
        while self.com.tx.getIsBussy() and time.time() < limite:
            time.sleep(0.002)
 
    def ressincronizar(self):
        """Joga fora tudo que estiver pela metade na linha.
 
        A camada fisica manda cada byte como DOIS caracteres hexadecimais.
        Se o fio cair bem no meio de um byte, sobra um "meio byte" (nibble)
        guardado em fisica.rxRemain - e a partir dai TODOS os bytes ficam
        deslocados em 4 bits, para sempre. O EOP nunca mais e encontrado e
        a transmissao trava mesmo depois de reconectar o fio.
 
        Este metodo zera esse lixo para a leitura voltar a ficar alinhada.
        So deve ser chamado quando ja estamos travados (nada de util chega
        ha varios segundos), porque ele descarta o que estiver no buffer.
        """
        self.buffer = b""
        fisica = getattr(self.com, "fisica", None)
        if fisica is None:                 # rodando no teste, sem serial
            return
        self.com.rx.threadPause()
        time.sleep(0.15)                   # deixa a leitura em curso acabar
        self.com.rx.clearBuffer()
        fisica.rxRemain = b""              # <- o meio byte que desalinha tudo
        self.com.rx.threadResume()
 
    def receber(self, timeout):
        """Espera um datagrama por ate 'timeout' segundos (None se estourar)."""
        limite = time.time() + timeout
        while True:
            n = self.com.rx.getBufferLen()
            if n:
                self.buffer += self.com.rx.getBuffer(n)
            datagrama, self.buffer = extrair(self.buffer)
            if datagrama is not None:
                tamanho_total = HEAD_SIZE + len(datagrama["payload"]) + len(EOP)
                self._registrar_log("receb", datagrama["tipo"], tamanho_total,
                                     num=datagrama["num"], total=datagrama["total"],
                                     crc=datagrama["crc"])
                return datagrama
            if time.time() >= limite:
                return None
            time.sleep(0.01)
 
 