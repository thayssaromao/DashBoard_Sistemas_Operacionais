import os
import pwd
import stat
import socket
import datetime # Para psutil e timestamp de criação (se psutil for usado)
import ctypes # p/ chamar semctl(2) via lib C
import ctypes.util # p/ encontrar a lib C
import struct

# Carrega a biblioteca C padrão para chamadas de sistema
libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True) #

# Variável global para armazenar informações globais de sockets de rede
_global_network_sockets_info = {} # Cache global para informações de sockets


# Estado interno para armazenar valores anteriores
prev_cpu_total = None    # armazena o último valor total lido do tempo da CPU (em jiffies)
                         # usado para calcular o delta entre leituras sucessivas
previo_processo_CPU = {}    # dicionário que mapeia ProcessoID de processos para seu último tempo total de CPU registrado
                         # usado para calcular a variação do tempo CPU por processo entre chamadas
delta_cpu_total = None    # armazena a diferença do tempo total da CPU entre a última e penúltima leitura
                         # usado para calcular percentuais relativos de uso da CPU
# MARK: Funções que lista todos os do sistema

def processosTodos():
    """Retorna e imprime uma lista de todos os PIDs encontrados em /proc"""

    processosID = [processosID for processosID in os.listdir('/proc') if processosID.isdigit()] #lista os processos do diretorio proc, verifica se todos os processos são dígitos.
  
    return processosID 


# MARK: Funções que lêem os dados de cada processo

def statusProcesso(processosID):

    """Lê e retorna os dados do arquivo /proc/[pid]/status como dicionário"""

    status_path = f'/proc/{processosID}/status' #caminho para acessar valores de cada processo
    status_info = {} #onde serão guardadas os valores do processo

    try: # Abre o arquivo status_path para leitura de forma segura,
    # garantindo que o arquivo será fechado automaticamente após o uso

        with open(status_path, 'r') as f: 

            for linha in f:#itera sobre cada linha de status do processo, 
                            #realiza uma condicional e armazena o valor de destaque no vetor status_info

                if linha.startswith("Name:"):
                    status_info["nome"] = linha.split()[1]

                elif linha.startswith("State:"):
                    status_info["estado"] = " ".join(linha.split()[1:])

                elif linha.startswith("Uid:"):
                    uid = int(linha.split()[1]) 
                    status_info["usuario"] = pwd.getpwuid(uid).pw_name #Acessa o banco de dados e pega o Usuario Id 
                                                                        #correspondente e seleciona o nome desse usuário
                                                                        
                elif linha.startswith("Threads:"):
                    status_info["threads"] = int(linha.split()[1])

                elif linha.startswith("VmSize:"):#memoria total
                    status_info["mem_total_kb"] = int(linha.split()[1])

                elif linha.startswith("VmRSS:"):#memoria residente
                    status_info["mem_residente_kb"] = int(linha.split()[1])

                elif linha.startswith("VmData:"):#memoria heap
                    status_info["mem_heap_kb"] = int(linha.split()[1])

                elif linha.startswith("VmStk:"):#memoria stack
                    status_info["mem_stack_kb"] = int(linha.split()[1])

                elif linha.startswith("VmExe:"):#memoria de código
                    status_info["mem_codigo_kb"] = int(linha.split()[1])

    except FileNotFoundError:
        print(f"Processo {processosID} não existe ou terminou.")
        return None
    
    except PermissionError:#exceção
        print(f"Sem permissão para acessar {status_path} (PermissionError)")
        return None

    return status_info # Retorna o dicionário com as informações do processo


# MARK: Funções que retornam dicionários com os dados de todos os processos ativos

def dicionarioStatusProcesso():
    """Retorna um dicionário com os status de todos os processos ativos, incluindo recursos abertos."""
    processos_info = {}
    # Atualiza o cache global de sockets de rede antes de processar os PIDs
    global _global_network_sockets_info # Declara uso da variável global
    _global_network_sockets_info = _ler_info_sockets_rede_global() # Preenche o cache global

    for processosID in processosTodos():
        info = statusProcesso(processosID)
        if info:
            # CORREÇÃO AQUI: Passar _global_network_sockets_info como argumento
            recursos_abertos = listar_recursos_abertos_processo(processosID, _global_network_sockets_info)
            info["recursos_abertos"] = recursos_abertos
            processos_info[processosID] = info
    return processos_info


# MARK: Funções que lêem o uso da CPU de cada processo

def cpuProcesso(processosID):

    """Lê e retorna os dados do arquivo /proc/[pid]/stat como dicionário"""

    stat_path = f'/proc/{processosID}/stat'
    
    try:

        with open(stat_path, 'r') as f:

            campos = f.read().split()

        utime = int(campos[13])     # campo 14: tempo em modo usuário
        stime = int(campos[14])     # campo 15: tempo em modo kernel

        tempo_total = utime + stime

        # Convertendo para segundos
        clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])  # normalmente 100
        tempo_segundos = tempo_total / clk_tck #calcula o tempo de jiffs em segundos

        return { 
            "utime_jiffies": utime,
            "stime_jiffies": stime,
            "tempo_total_jiffies": tempo_total,
            "tempo_total_segundos": round(tempo_segundos, 2)
        }
    
    except FileNotFoundError:
        print(f"Processo {processosID} não encontrado.")
        return None
    
    except PermissionError:
        print(f"Sem permissão para acessar {stat_path}.")
        return None    


#prev_cpu_total = None 
#previo_processo_CPU = {}    
#delta_cpu_total = None   

# MARK: Funções que lêem o tempo total da CPU do sistema em jiffies

def ler_cpu_total(): 

    with open("/proc/stat", "r") as f: #caminho com o valor do uso da cpu global

        linha = f.readline()
        valores = list(map(int, linha.split()[1:])) # converte os tempos para inteiros, ignorando o label 'cpu'
        return sum(valores) # soma e retorna o total dos jiffies da CPU


# MARK: Funções que atualizam o uso da CPU e processos calculando o delta 

def atualizar_cpu_total():

    """Atualiza o delta(diferença) global do tempo total da CPU. Deve ser chamado uma vez por ciclo."""

    global prev_cpu_total, delta_cpu_total # usa as variáveis globais para manter o estado entre chamadas

    cpu_total_atual = ler_cpu_total() # lê o valor total atual do tempo da CPU (em jiffies)

    if prev_cpu_total is None: # se for a primeira vez que a função é chamada

        prev_cpu_total = cpu_total_atual # armazena o valor atual como referência para próximas leituras
        delta_cpu_total = 0  # delta inicial é zero, pois não há leitura anterior para comparar
        return 0  # retorna zero pois não há variação calculável ainda
    
    delta_cpu_total = cpu_total_atual - prev_cpu_total # calcula a diferença desde a última leitura
    prev_cpu_total = cpu_total_atual # atualiza o valor armazenado para próxima chamada

    return delta_cpu_total  # retorna o delta calculado (variação do tempo CPU)


# MARK: Funções que calculam o uso da CPU por processo

def calcular_uso_cpu_processo(pid):

    global previo_processo_CPU, delta_cpu_total  # usa variáveis globais para manter dados entre chamadas

    proc_info = cpuProcesso(pid) # obtém informações atuais do processo pelo ProcessoID

    if proc_info is None: #se o processo não existir ou info não disponível
        return 0.0 #retorna uso 0.0 para evitar erro
    
    proc_total_atual = proc_info['tempo_total_jiffies'] # extrai o tempo total da CPU usado pelo processo (em jiffies)

    if pid not in previo_processo_CPU:  # se for a primeira vez que vemos esse ProcessoID

        previo_processo_CPU[pid] = proc_total_atual # armazena o tempo atual para próximas comparações
        return 0.0  # retorna 0.0 pois não tem dado anterior para calcular delta

    delta_processos = proc_total_atual - previo_processo_CPU[pid] # calcula a variação do tempo CPU do processo
    previo_processo_CPU[pid] = proc_total_atual # atualiza o valor armazenado para a próxima chamada

    if delta_cpu_total and delta_cpu_total > 0:  # verifica se o delta total da CPU está disponível e é válido
        uso = (delta_processos / delta_cpu_total) * 100 
    else:
        uso = 0.0 # se não tiver delta válido, considera uso 0
        uso = 0.0 # se não tiver delta válido, considera uso 0

    return round(uso, 2)  # retorna o uso arredondado com 2 casas decimais
    return round(uso, 2)  # retorna o uso arredondado com 2 casas decimais


# MARK: Funções que retornam dicionários com o uso da CPU de todos os processos ativos

def dicionarioStatCPUProcesso():

    """Retorna um dicionário com o uso percentual da CPU de todos os processos ativos"""
    processosCPU_info = {}  

    for pid in processosTodos():  

        uso_percentual = calcular_uso_cpu_processo(pid) # calcula o uso percentual da CPU para o processo atual

        tempo_cpu = cpuProcesso(pid)  # obtém as informações detalhadas do uso de CPU do processo

        if tempo_cpu is not None:  # verifica se as informações do processo foram obtidas com sucesso

            processosCPU_info[pid] = { # adiciona ao dicionário o PID como chave e um novo dicionário com as infos + uso percentual
                **tempo_cpu,  # copia todas as informações do tempo_cpu para o novo dicionário o ** desempacota o dicionário
                "uso_percentual_cpu": uso_percentual  # adiciona o uso percentual de CPU como um campo extra
            }
    return processosCPU_info # retorna o dicionário com os dados de todos os processos ativos

# MARK: Funções que lêem a quantidade de páginas usadas por cada processo

def paginaProcesso(processosID):

    """Lê e retorna a quantidade de páginas usadas pelo processo a partir do arquivo /proc/[pid]/statm"""
    
    statm_path = f'/proc/{processosID}/statm'  

    try:

        with open(statm_path, 'r') as f:
            # Lê o conteúdo e divide os valores
            campos = f.read().split()
        
        total_pagina = int(campos[0])  # O primeiro campo de statm é o tamanho total do processo em páginas (tamanho virtual)

        return {
            "total_pagina": total_pagina 
        }
    
    except FileNotFoundError:  
        print(f"Processo {processosID} não encontrado.")
        return None
    
    except PermissionError:  
        print(f"Sem permissão para acessar {statm_path}.")
        return None


# MARK: Funções que retornam dicionários com a quantidade de páginas usadas por todos os processos ativos

def dicionarioPaginaProcesso():
    """Retorna um dicionário com a quantidade de páginas usadas por todos os processos ativos"""
    processos_pagina_info = {}  # Dicionário onde serão armazenadas as informações de páginas dos processos
    for processosID in processosTodos(): 
        pagina_info = paginaProcesso(processosID)  # Chama a função que retorna a quantidade de páginas
        if pagina_info:  
            processos_pagina_info[processosID] = pagina_info  # Adiciona o PID e a quantidade de páginas no dicionário
    return processos_pagina_info  # Retorna o dicionário com as informações de páginas de todos os processos


# MARK: Função que conta o número total de processos e threads ativos

def contar_processos_e_threads():
    """
    Calcula o número total de processos ativos e o número total de threads.
    Retorna uma tupla: (total_processos, total_threads).
    """
    lista_pids = processosTodos()
    
    if not lista_pids:
        return 0, 0

    total_processos = len(lista_pids)
    total_threads = 0

    for pid in lista_pids:
        info_proc = statusProcesso(pid) # retorna informações do processo como dicionário

        if info_proc and "threads" in info_proc:
            try:
                total_threads += int(info_proc["threads"]) 
            except (ValueError, TypeError):
                # Ocorre se 'threads' não for um número válido; ignora o processo para a soma de threads.
                pass 
    return total_processos, total_threads


# ----------------------- helpers POSIX ----------------------------
def list_posix_named_semaphores():
    """
    Escaneia /dev/shm em busca de semáforos POSIX nomeados (arquivos 'sem.*').
    Retorna lista de dicts com caminho, inode e permissões.
    """
    sems = []
    shm_path = "/dev/shm"
    try:
        if os.path.exists(shm_path) and os.path.isdir(shm_path):
            for fname in os.listdir(shm_path):
                if not fname.startswith("sem."):
                    continue
                full = os.path.join(shm_path, fname)
                try:
                    st = os.stat(full)
                    sems.append({
                        "tipo": "POSIX Nomeado",
                        "caminho": full,
                        "inode": st.st_ino,
                        "perms": oct(st.st_mode & 0o777),
                        "tamanho": st.st_size
                    })
                except (FileNotFoundError, PermissionError):
                    pass # Ignora arquivos que não podem ser acessados
    except (FileNotFoundError, PermissionError):
        pass # Ignora se /dev/shm não existe ou não pode ser acessado
    return sems

# ------------------- helpers para classificação de FD e Sockets ----------------------
def _tipo_recurso_sem(real_path, target_stat):
    """
    Detecta se o real_path de um descritor de arquivo corresponde a semáforo POSIX anônimo ou nomeado.
    """
    if real_path.startswith('anon_inode:[sem]'): #
        return "POSIX Anônimo (Semaphore)"
    if real_path.startswith('/dev/shm/sem.'): #
        return "POSIX Nomeado (Semaphore)"
    return None

def _ler_info_sockets_rede_global():
    """
    Lê informações de sockets de rede globais do sistema de /proc/net/.
    Retorna um dicionário mapeando inodes de socket para seus detalhes.
    """
    sockets_info = {}
    socket_files = {
        "tcp": "/proc/net/tcp",
        "udp": "/proc/net/udp",
        "tcp6": "/proc/net/tcp6",
        "udp6": "/proc/net/udp6",
    }

    for proto, path in socket_files.items():
        try:
            with open(path, 'r') as f:
                next(f)
                for line in f:
                    parts = line.strip().split()
                    if len(parts) < 10:
                        continue
                    try:
                        local_addr_hex, local_port_hex = parts[1].split(':')
                        rem_addr_hex, rem_port_hex = parts[2].split(':')
                        st = int(parts[3], 16)
                        inode = int(parts[9])

                        local_ip = ''
                        if len(local_addr_hex) == 8: # IPv4
                            local_ip = socket.inet_ntoa(struct.pack("<L", int(local_addr_hex, 16))) #
                        elif len(local_addr_hex) == 32: # IPv6
                            local_ip = socket.inet_ntop(socket.AF_INET6, bytes.fromhex(local_addr_hex))
                        else:
                            local_ip = "N/A"

                        local_port = int(local_port_hex, 16)

                        remote_ip = ''
                        if len(rem_addr_hex) == 8: # IPv4
                
                            remote_ip = socket.inet_ntoa(struct.pack("<L", int(rem_addr_hex, 16))) #
                        elif len(rem_addr_hex) == 32: # IPv6
                            remote_ip = socket.inet_ntop(socket.AF_INET6, bytes.fromhex(rem_addr_hex))
                        else:
                            remote_ip = "N/A"

                        remote_port = int(rem_port_hex, 16)

                        sockets_info[inode] = {
                            "protocolo": proto,
                            "local_address": f"{local_ip}:{local_port}",
                            "remote_address": f"{remote_ip}:{remote_port}",
                            "state": _get_socket_state_name(st),
                            "inode": inode
                        }
                    except (ValueError, IndexError, socket.error, struct.error): # Adicionado struct.error
                        continue
        except (FileNotFoundError, PermissionError):
            continue
    return sockets_info

# _get_socket_state_name 
def _get_socket_state_name(state_hex_int):
    # Conteúdo da sua função _get_socket_state_name
    states = {
        1: "ESTABLISHED", 2: "SYN_SENT", 3: "SYN_RECV", 4: "FIN_WAIT1",
        5: "FIN_WAIT2", 6: "TIME_WAIT", 7: "CLOSE", 8: "CLOSE_WAIT",
        9: "LAST_ACK", 10: "LISTEN", 11: "CLOSING", 12: "NEW_SYN_RECV"
    }
    return states.get(state_hex_int, f"UNKNOWN({state_hex_int})")


# MARK: Função para listar recursos abertos por processo 
def listar_recursos_abertos_processo(pid, global_network_sockets_info):
    """
    Lista os descritores de arquivo abertos por um processo lendo o diretório /proc/[pid]/fd.
    Para cada descritor, tenta determinar o tipo (arquivo, socket, semáforo POSIX, pipe, etc.) e o caminho/identificador.
    Utiliza informações globais de sockets para detalhamento.
    Retorna um dicionário com listas de recursos categorizados.
    """
    recursos_abertos = {
        'pid': pid,
        'arquivos_regulares': [],
        'sockets': [],
        'pipes': [],
        'dispositivos': [],
        'semaphores_posix': [], # Para semáforos POSIX detectados via FD
        'links_quebrados_ou_inacessiveis': [],
        'outros': []
    }
    fd_path = f'/proc/{pid}/fd'

    try:
        for fd_num_str in os.listdir(fd_path):
            fd_num = int(fd_num_str)
            full_fd_path = os.path.join(fd_path, fd_num_str)

            try:
                real_path = os.readlink(full_fd_path)
                
                # Tenta obter o stat do ALVO do link simbólico
                target_stat = None
                try:
                    # os.stat segue o link simbólico
                    target_stat = os.stat(full_fd_path)
                except (FileNotFoundError, PermissionError):
                    pass # Se o alvo não existe ou sem permissão, target_stat permanece None

                tipo_recurso = "Desconhecido"
                detalhes = {
                    'fd': fd_num,
                    'caminho': real_path,
                    'modo': oct(target_stat.st_mode) if target_stat else 'N/A',
                    'inode': target_stat.st_ino if target_stat else 'N/A',
                    'tamanho': target_stat.st_size if target_stat and os.path.exists(real_path) else 'N/A',
                }

                # Prioridade na classificação: Semáforos POSIX, Sockets, Pipes, Dispositivos, Arquivos
                sem_tipo = _tipo_recurso_sem(real_path, target_stat)
                if sem_tipo:
                    detalhes['tipo'] = sem_tipo
                    recursos_abertos['semaphores_posix'].append(detalhes)
                elif real_path.startswith('socket:['): # Socket de domínio Unix ou outro
                    inode = int(real_path.split('[')[1][:-1]) if '[' in real_path else 'N/A'
                    socket_info = global_network_sockets_info.get(inode)
                    if socket_info:
                        detalhes['tipo'] = "Socket de Rede"
                        detalhes.update(socket_info)
                        recursos_abertos['sockets'].append(detalhes)
                    else:
                        detalhes['tipo'] = "Socket (Unix/Outro)"
                        recursos_abertos['sockets'].append(detalhes)
                elif real_path.startswith('pipe:['):
                    detalhes['tipo'] = "Pipe (FIFO)"
                    recursos_abertos['pipes'].append(detalhes)
                elif target_stat:
                    if stat.S_ISREG(target_stat.st_mode):
                        detalhes['tipo'] = "Arquivo Regular"
                        recursos_abertos['arquivos_regulares'].append(detalhes)
                    elif stat.S_ISDIR(target_stat.st_mode):
                        detalhes['tipo'] = "Diretório"
                        recursos_abertos['arquivos_regulares'].append(detalhes) # Pode ser uma categoria separada se quiser
                    elif stat.S_ISCHR(target_stat.st_mode):
                        detalhes['tipo'] = "Dispositivo de Caractere"
                        recursos_abertos['dispositivos'].append(detalhes)
                    elif stat.S_ISBLK(target_stat.st_mode):
                        detalhes['tipo'] = "Dispositivo de Bloco"
                        recursos_abertos['dispositivos'].append(detalhes)
                    elif stat.S_ISLNK(target_stat.st_mode):
                         # O alvo do link é outro link, tratamos como arquivo regular por simplicidade aqui
                        detalhes['tipo'] = "Link Simbólico (alvo)"
                        recursos_abertos['arquivos_regulares'].append(detalhes)
                    else:
                        detalhes['tipo'] = "Outro"
                        recursos_abertos['outros'].append(detalhes)
                else:
                    detalhes['tipo'] = "Link Quebrado/Inacessível"
                    recursos_abertos['links_quebrados_ou_inacessiveis'].append(detalhes)

            except (OSError, ValueError) as e:
                # Captura erros ao lerlink ou stat, indicando um link quebrado ou inacessível
                recursos_abertos['links_quebrados_ou_inacessiveis'].append({
                    'fd': fd_num_str,
                    'caminho': f"Erro ao ler link: {e}",
                    'tipo': 'Link Quebrado/Inacessível'
                })
    except (FileNotFoundError, PermissionError):
        pass # Diretório /proc/{pid}/fd não existe ou sem permissão

    return recursos_abertos
