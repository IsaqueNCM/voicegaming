# VoiceGaming_SWITCH_REFACTOR_FINAL.py - Microfone virtual, Soundboard, Baixa Latência e Monitoramento Condicional
#
# NECESSÁRIO: Instalar numpy, sounddevice, soundfile, PyQt5, scipy E keyboard
# pip install numpy sounddevice soundfile PyQt5 scipy keyboard
#
import sys
import os
import numpy as np
import sounddevice as sd
import soundfile as sf
import threading
import queue
import json 
import keyboard 
from PyQt5 import QtWidgets, QtCore, QtGui
from scipy.signal import resample_poly

# ==================== CONFIGURAÇÕES GLOBAIS (CORES E ÁUDIO) ====================
# Taxa de amostragem (44100 Hz recomendado para USB/VB-CABLE)
SAMPLERATE = 44100
BLOCKSIZE = 512 
CHANNELS = 1
CONFIG_FILE = 'config.json'
ICON_PATH = 'logo.png' # Assumindo que o arquivo de ícone está na mesma pasta

# Esquema de Cores Neon
COLOR_BACKGROUND = '#1a1a1a'
COLOR_TEXT_NORMAL = '#ffffff'
COLOR_ACCENT_MIC = '#00ff88'  # Verde Neon
COLOR_ACCENT_AUDIO = '#00ffff' # Ciano Neon
COLOR_WARNING = '#ffdd00'     # Amarelo/Laranja
COLOR_ERROR = '#ff0000'
COLOR_BORDER = '#333333'       # Borda discreta para grupos

# Filas e flags de controle
output_queue = queue.Queue(maxsize=100)
monitor_queue = queue.Queue(maxsize=100) 
global_main_window = None
mode_voice = True      
playing_music = False
stop_music_event = threading.Event()
input_stream = None
output_stream = None
monitor_stream = None 
music_volume_factor = 0.8 
mic_volume_factor = 1.0 
monitor_volume_factor = 0.5 
current_soundboard_key = None     
soundboard_stop_event = None      
SOUNDBOARD_SHORTCUTS = {} 

# --- Funções de persistência e callbacks de áudio ---

def save_config(input_idx, output_idx, monitor_idx, volume, mic_volume, monitor_volume, shortcuts, soundboard_folder):
    """Salva a configuração atual em um arquivo JSON."""
    config = {
        'input_device_index': input_idx,
        'output_device_index': output_idx,
        'monitor_device_index': monitor_idx, 
        'volume_level': volume,
        'mic_volume_level': mic_volume,
        'monitor_volume_level': monitor_volume, 
        'soundboard_shortcuts': shortcuts,
        'soundboard_folder': soundboard_folder
    }
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Erro ao salvar configuração: {e}", file=sys.stderr)

def load_config():
    """Carrega a configuração de um arquivo JSON."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao carregar configuração ({CONFIG_FILE}): {e}", file=sys.stderr)
            return {}
    return {} 

def input_callback(indata, frames, time, status):
    global mode_voice
    
    if mode_voice and not playing_music:
        processed_data = indata * mic_volume_factor
        output_queue.put(processed_data.copy())

def output_callback(outdata, frames, time, status):
    global monitor_queue
        
    try:
        data = output_queue.get_nowait()
    except queue.Empty:
        data = np.zeros((frames, CHANNELS), dtype='float32')

    if data.shape[0] != frames:
        # Padding ou Truncamento
        if data.shape[0] < frames:
             data = np.pad(data, ((0, frames - data.shape[0]), (0, 0))) 
        else:
             data = data[:frames] # Em caso de Buffer grande 
    
    try:
        monitor_queue.put_nowait(data.copy())
    except queue.Full:
        pass 
        
    peak = np.max(np.abs(data))
    if peak > 0.95: 
        data = data * (0.95 / peak)
    
    outdata[:] = data

def monitor_callback(outdata, frames, time, status):
    try:
        data = monitor_queue.get_nowait()
    except queue.Empty:
        data = np.zeros((frames, CHANNELS), dtype='float32')
        
    if data.shape[0] != frames:
        if data.shape[0] < frames:
            data = np.pad(data, ((0, frames - data.shape[0]), (0, 0)))
        else:
            data = data[:frames]
        
    outdata[:] = data * monitor_volume_factor

def play_audio_thread(filepath, is_music, status_update_callback, hotkey=None, stop_event=None):
    """Função genérica para tocar áudio em thread separada."""
    global playing_music, mode_voice, current_soundboard_key, global_main_window
    
    if not os.path.exists(filepath):
        status_update_callback("Erro: Arquivo não encontrado.", COLOR_ERROR)
        if not is_music:
            mode_voice = True 
            current_soundboard_key = None
            if global_main_window:
                global_main_window.update_monitor_stream_state()
            while not output_queue.empty():
                try: output_queue.get_nowait()
                except: pass
        return

    # MÚSICA PRINCIPAL
    if is_music:
        stop_music_event.clear()
        playing_music = True
        mode_voice = False 
        status_update_callback("MÚSICA Principal: Tocando → voz pausada", COLOR_ACCENT_AUDIO)
        
    # SOUNDBOARD
    else:
        mode_voice = False 
        status_update_callback(f"Soundboard: Tocando atalho {hotkey} ({os.path.basename(filepath)}) → voz pausada", COLOR_ACCENT_AUDIO)

    if global_main_window:
        global_main_window.update_monitor_stream_state()

    try:
        audio, sr = sf.read(filepath, dtype='float32')
        
        # Converte para mono
        if len(audio.shape) > 1 and audio.shape[1] > 1:
            audio = np.mean(audio, axis=1)
            
        # Resample para a taxa de amostragem do stream de saída (VB-CABLE)
        target_sr = global_main_window.get_output_samplerate() if global_main_window else SAMPLERATE
        if sr != target_sr:
            audio = resample_poly(audio, target_sr, sr).astype(np.float32)
            
        # Aplica volume
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * music_volume_factor

        # Loop de reprodução
        pos = 0
        stop_condition = lambda: (is_music and stop_music_event.is_set()) or (not is_music and stop_event.is_set())

        while pos < len(audio) and not stop_condition():
            end = pos + BLOCKSIZE
            block = audio[pos:end]
            
            if len(block) < BLOCKSIZE:
                block = np.pad(block, (0, BLOCKSIZE - len(block)))
            
            if block.ndim == 1:
                block = block.reshape(-1, 1)
            
            output_queue.put(block)
            
            pos = end

    except Exception as e:
        status_update_callback(f"Erro no áudio: {e}", COLOR_ERROR)
    
    finally:
        while not output_queue.empty():
            try: output_queue.get_nowait()
            except: pass
            
        if is_music:
            playing_music = False
            status_update_callback("Música parada/finalizada → voltando sua voz...", COLOR_ACCENT_MIC)
            
        else:
            is_cancelled = stop_event.is_set()
            current_soundboard_key = None 
            
            if is_cancelled:
                status_update_callback(f"Soundboard ({hotkey}) CANCELADO → voltando sua voz...", COLOR_ACCENT_MIC)
            else:
                status_update_callback(f"Soundboard ({hotkey}) finalizado → voltando sua voz...", COLOR_ACCENT_MIC)
        
        mode_voice = True 
        
        if global_main_window:
            global_main_window.update_monitor_stream_state()

# ==================== CONTROLES DE WIDGETS PERSONALIZADOS ====================

class NoScrollSlider(QtWidgets.QSlider):
    """QSlider que ignora eventos de roda do mouse."""
    def wheelEvent(self, event):
        event.ignore()

# ==================== INTERFACE GRÁFICA (PyQt5) ====================

class VoiceGamingSWITCH(QtWidgets.QMainWindow):
    status_signal = QtCore.pyqtSignal(str, str)
    hotkey_signal = QtCore.pyqtSignal(str) 

    def __init__(self):
        super().__init__()
        self.config = load_config()
        
        global global_main_window, music_volume_factor, mic_volume_factor, monitor_volume_factor, SOUNDBOARD_SHORTCUTS
        global_main_window = self 

        SOUNDBOARD_SHORTCUTS = self.config.get('soundboard_shortcuts', {})
        self.musica_path = SOUNDBOARD_SHORTCUTS.get('0')

        self.device_info = sd.query_devices()
        self.input_devices = [d for d in self.device_info if d['max_input_channels'] > 0]
        self.output_devices = [d for d in self.device_info if d['max_output_channels'] > 0]
        self.monitor_devices = [d for d in self.device_info if d['max_output_channels'] > 0]
        
        # Dicionário para armazenar a taxa de amostragem padrão dos dispositivos selecionados
        self.device_sample_rates = {
            'input': self.get_device_default_samplerate(self.config.get('input_device_index', -1)),
            'output': self.get_device_default_samplerate(self.config.get('output_device_index', -1)),
            'monitor': self.get_device_default_samplerate(self.config.get('monitor_device_index', -1)),
        }
        
        self.volume_level = self.config.get('volume_level', 80)
        music_volume_factor = self.volume_level / 100.0
        
        self.mic_level = self.config.get('mic_volume_level', 100) 
        mic_volume_factor = self.mic_level / 100.0
        
        self.monitor_level = self.config.get('monitor_volume_level', 50) 
        monitor_volume_factor = self.monitor_level / 100.0
        
        self.soundboard_folder = self.config.get('soundboard_folder', '') 
        
        # ONDE O ERRO OCORRIA: Chamando a função para mapear a pasta
        if self.soundboard_folder and os.path.isdir(self.soundboard_folder):
             self._map_folder_to_shortcuts(initial_load=True) 

        self.setWindowTitle("🎤 VoiceGaming SWITCH 🎶")
        self.setGeometry(200, 100, 750, 650) # Tamanho padrão maior e fixo
        
        
        self.setStyleSheet(f"""
            background:{COLOR_BACKGROUND}; 
            color:{COLOR_TEXT_NORMAL}; 
            font-family: 'Segoe UI', Consolas, sans-serif;
            
            QTabWidget::pane {{ border: 1px solid {COLOR_BORDER}; }} 
            QTabBar::tab {{ 
                background: #444444;       /* Fundo: Cinza Escuro (Mude aqui a cor de fundo INATIVA) */
                color: #ffffff;            /* Texto: Branco (Mude aqui a cor do texto INATIVO) */
                padding: 10px 20px; 
                min-width: 150px;
                font-weight: bold;
                border: none; /* ESSENCIAL: Remove bordas nativas */
                border-bottom: 3px solid #444444; /* Borda da cor de fundo da aba */
                margin-right: 5px; /* Adiciona um pequeno espaço entre as abas */
            }}
            QTabBar::tab:selected {{ 
                background: {COLOR_ACCENT_MIC}; /* Fundo: Usa o Verde Neon ou a cor que você definir */
                color: black;                   /* Texto: Preto para contraste (Mude aqui a cor do texto ATIVO) */
                border: none;
                border-bottom: 3px solid {COLOR_ACCENT_MIC}; /* Força uma cor de borda para baixo para igualar o fundo */
                /* Adicione uma linha de destaque superior se quiser */
                border-top: 3px solid #FF00FF; /* Exemplo: linha superior rosa neon */
            }}
            QScrollArea {{ border: none; }}
            QGroupBox {{ 
                border: 1px solid {COLOR_BORDER}; 
                margin-top: 10px; 
                padding-top: 15px;
                color: {COLOR_TEXT_NORMAL};
            }}
            QGroupBox::title {{ 
                subcontrol-origin: margin; 
                subcontrol-position: top center; 
                padding: 0 5px; 
            }}
        """)
        
        self.setup_tray_icon()
        self.setup_ui()
        self.status_signal.connect(self.update_status_ui)
        self.hotkey_signal.connect(self.play_soundboard_audio) 
        self.setup_hotkeys()
        
    def get_device_default_samplerate(self, index):
        """Busca a taxa de amostragem padrão de um dispositivo pelo índice."""
        if index == -1:
            return SAMPLERATE # Default para 44100 se não selecionado
        try:
            return int(sd.query_devices(index)['default_samplerate'])
        except Exception:
            return SAMPLERATE
            
    def get_input_samplerate(self): return self.device_sample_rates.get('input', SAMPLERATE)
    def get_output_samplerate(self): return self.device_sample_rates.get('output', SAMPLERATE)
    def get_monitor_samplerate(self): return self.device_sample_rates.get('monitor', SAMPLERATE)

    def save_current_config(self, save_devices=False):
        """Salva a configuração atual."""
        input_idx = self.config.get('input_device_index', -1)
        output_idx = self.config.get('output_device_index', -1)
        monitor_idx = self.config.get('monitor_device_index', -1)

        if save_devices and hasattr(self, 'input_combo') and self.input_combo.isVisible():
            input_idx = self.input_combo.currentData()
            output_idx = self.output_combo.currentData()
            monitor_idx = self.monitor_combo.currentData()
            
        if self.musica_path:
            SOUNDBOARD_SHORTCUTS['0'] = self.musica_path
        else:
            SOUNDBOARD_SHORTCUTS.pop('0', None) 
            
        valid_shortcuts = {k: v for k, v in SOUNDBOARD_SHORTCUTS.items() if v or k == '0'}
        
        save_config(input_idx, output_idx, monitor_idx, self.volume_level, self.mic_level, self.monitor_level, valid_shortcuts, self.soundboard_folder)
        
    # --- UI Setup ---
    def setup_ui(self):
        """Configura a interface principal usando QTabWidget."""
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # 1. Título e Info - Nome Alterado
        title = QtWidgets.QLabel("🎤 VoiceGaming SWITCH 🎶")
        title.setAlignment(QtCore.Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 24px; font-weight: bold; color:{COLOR_ACCENT_MIC}; margin-bottom: 10px;")
        main_layout.addWidget(title)
        
        info = QtWidgets.QLabel(
            f"✅ **Status:** Use os atalhos HOME+N no teclado para tocar os efeitos.\n"
            f"⚠️ Selecione a saída **CABLE Input** (Microfone Virtual) no seu App/Jogo."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"background:#222; padding:10px; border-radius:8px; border: 1px solid {COLOR_ACCENT_AUDIO}; color: {COLOR_ACCENT_AUDIO}; margin-bottom: 15px;")
        main_layout.addWidget(info)
        
        # 2. Botão Start/Stop Principal
        self.btn_start_stop = QtWidgets.QPushButton("INICIAR AUDIO STREAMS (Ativar)")
        self.btn_start_stop.clicked.connect(self.toggle_streams)
        self.btn_start_stop.setStyleSheet(f"padding:15px; background:{COLOR_ACCENT_MIC}; color:black; font-weight:bold; font-size:18px; border-radius: 10px; margin-bottom: 15px;")
        main_layout.addWidget(self.btn_start_stop)
        
        # 3. Tab Widget
        self.tab_widget = QtWidgets.QTabWidget()
        self.tab_widget.currentChanged.connect(self._handle_tab_change) # Conecta para salvar/aplicar
        main_layout.addWidget(self.tab_widget)
        
        # --- Aba 1: Soundboard Principal ---
        self.soundboard_tab = self._create_soundboard_tab()
        self.tab_widget.addTab(self.soundboard_tab, "🔊 Efeitos")
        
        # --- Aba 2: Configurações ---
        self.config_tab = self._create_config_tab()
        self.tab_widget.addTab(self.config_tab, "⚙️ Configurações")
        
        # 4. Status Bar
        self.status = QtWidgets.QLabel("Status: Pressione 'INICIAR AUDIO STREAMS'")
        self.status.setStyleSheet(f"color:{COLOR_WARNING}; font-size:14px; padding:10px; background:#222; border-radius: 8px; margin-top: 15px;")
        main_layout.addWidget(self.status)
        
        self._update_soundboard_ui_from_config()
        self._update_start_stop_ui()
        
    def _handle_tab_change(self, index):
        """Gerencia a troca de abas para salvar configurações automaticamente."""
        # Se a aba anterior era a de Configurações (index 1), salva e tenta aplicar
        if self.tab_widget.widget(index) == self.soundboard_tab:
            # 1. Salva as configurações de dispositivo (Novos índices e taxas)
            self._apply_and_save_config()
            
    def _create_header(self, text):
        label = QtWidgets.QLabel(f"--- {text} ---")
        label.setStyleSheet(f"font-weight: bold; font-size: 14px; margin-top: 10px; color: {COLOR_ACCENT_MIC};")
        return label
        
    # --- Aba Soundboard ---
    def _create_soundboard_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        # 1. Controles Principais (Música)
        music_wrapper = QtWidgets.QWidget()
        music_layout = QtWidgets.QHBoxLayout(music_wrapper)
        music_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_play = QtWidgets.QPushButton("🎵 Tocar/Parar Música (HOME + 0)")
        self.btn_play.setStyleSheet(f"padding:15px; font-size:14px; background:{COLOR_ACCENT_AUDIO}; color:black; font-weight: bold; border-radius: 8px;")
        self.btn_play.clicked.connect(lambda: self.toggle_music('0'))
        self.btn_play.setEnabled(False) 
        music_layout.addWidget(self.btn_play)

        
        layout.addWidget(music_wrapper)

        # 2. Botões de Efeito Rápido (Soundboard)
        soundboard_group = QtWidgets.QGroupBox("Botões de Efeito Rápido (HOME + Tecla)")
        soundboard_group.setStyleSheet(f"QGroupBox {{ color:{COLOR_ACCENT_AUDIO}; border: 1px solid {COLOR_BORDER}; margin-top: 10px; }} QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top center; padding: 0 5px; }}")
        
        self.soundboard_grid_layout = QtWidgets.QGridLayout() 
        self.soundboard_grid_layout.setSpacing(5) # REDUÇÃO DO ESPAÇAMENTO
        
        soundboard_content = QtWidgets.QWidget()
        soundboard_content.setLayout(self.soundboard_grid_layout)

        self.soundboard_scroll = QtWidgets.QScrollArea()
        self.soundboard_scroll.setWidgetResizable(True)
        self.soundboard_scroll.setWidget(soundboard_content)
        self.soundboard_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff) 
        self.soundboard_scroll.setStyleSheet(f"QScrollArea {{ border: none; background: transparent; }}")
        
        soundboard_v_layout = QtWidgets.QVBoxLayout(soundboard_group)
        soundboard_v_layout.addWidget(self.soundboard_scroll)
        
        layout.addWidget(soundboard_group, 1) # Adiciona o grupo e permite expandir

        # 3. Gerenciamento de Arquivos
        sb_buttons_layout = QtWidgets.QHBoxLayout()
        
        btn_select_folder = QtWidgets.QPushButton("📁 Mapear Pasta")
        btn_select_folder.clicked.connect(self.select_soundboard_folder)
        btn_select_folder.setStyleSheet(f"padding:10px; background:#444; color:{COLOR_TEXT_NORMAL}; font-weight:bold; border-radius: 8px;")
        sb_buttons_layout.addWidget(btn_select_folder)
        
        btn_add_shortcut = QtWidgets.QPushButton("➕ Novo Atalho")
        btn_add_shortcut.clicked.connect(self.add_shortcut_dialog)
        btn_add_shortcut.setStyleSheet(f"padding:10px; background:#444; color:{COLOR_TEXT_NORMAL}; font-weight:bold; border-radius: 8px;")
        sb_buttons_layout.addWidget(btn_add_shortcut)
        
        layout.addLayout(sb_buttons_layout)
        
        return tab

    # --- Aba Configurações ---
    def _create_config_tab(self):
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(15, 15, 15, 15)
        
        self.scroll_area_config = QtWidgets.QScrollArea()
        self.scroll_area_config.setWidgetResizable(True)
        
        self.config_container = QtWidgets.QWidget()
        self.config_layout = QtWidgets.QVBoxLayout(self.config_container)
        self.config_layout.setSpacing(15)
        
        self._setup_device_volume_section()
        self._setup_soundboard_management_section()
        
        self.config_layout.addStretch(1)
        self.scroll_area_config.setWidget(self.config_container)
        
        layout.addWidget(self.scroll_area_config)
        
        self.current_hotkey_dialog = None # Para o input do atalho
        
        return tab

    def _apply_and_save_config(self):
        """
        Salva e aplica as configurações do diálogo quando a aba de Configurações é fechada.
        """
        if not hasattr(self, 'input_combo'): return # Se o diálogo não foi aberto

        # 1. Salva as configurações (incluindo dispositivos e volumes atualizados)
        self.save_current_config(save_devices=True)
        
        # 2. Atualiza as taxas de amostragem
        new_input_idx = self.config.get('input_device_index', -1)
        new_output_idx = self.config.get('output_device_index', -1)
        new_monitor_idx = self.config.get('monitor_device_index', -1)
        
        self.device_sample_rates = {
            'input': self.get_device_default_samplerate(new_input_idx),
            'output': self.get_device_default_samplerate(new_output_idx),
            'monitor': self.get_device_default_samplerate(new_monitor_idx),
        }
        
        # 3. Aplica mudanças (Hotkeys e UI)
        self.setup_hotkeys()
        self._update_soundboard_ui_from_config()
        
        # 4. Notifica o usuário e sugere reinício do stream
        self.update_status_ui("Configurações salvas automaticamente. Streams DEVERÃO ser reiniciados para aplicar novos volumes/dispositivos.", COLOR_WARNING)


    # --- Seção 1 & 2: Dispositivos e Volumes ---
    def _setup_device_volume_section(self):
        self.config_layout.addWidget(self._create_header("1. Seleção de Dispositivos (Reinicie os streams para aplicar)"))
        
        self.input_combo, input_wrapper = self._create_device_combo(self.input_devices, 'Microfone Real 🎙️')
        default_in = self.config.get('input_device_index', sd.default.device[0] if sd.default.device else -1)
        self._set_default_device(self.input_combo, default_in)
        self.config_layout.addWidget(input_wrapper)

        self.output_combo, output_wrapper = self._create_device_combo(self.output_devices, 'Saída Virtual (VB-CABLE) 🎤')
        default_out = self.config.get('output_device_index', sd.default.device[1] if sd.default.device else -1)
        self._set_default_device(self.output_combo, default_out)
        self.config_layout.addWidget(output_wrapper)
        
        self.monitor_combo, monitor_wrapper = self._create_device_combo(self.monitor_devices, 'Saída Monitor (Fones) 🎧')
        default_monitor = self.config.get('monitor_device_index', sd.default.device[1] if sd.default.device else -1)
        self._set_default_device(self.monitor_combo, default_monitor)
        self.config_layout.addWidget(monitor_wrapper)

        self.config_layout.addWidget(self._create_header("2. Controles de Volume")) 
        
        mic_volume_layout = self._create_volume_slider("Volume Microfone Principal 🎙️:", self.mic_level, self.update_mic_volume)
        self.config_layout.addLayout(mic_volume_layout)
        
        sb_volume_layout = self._create_volume_slider("Volume Soundboard/Música 🎵:", self.volume_level, self.update_music_volume)
        self.config_layout.addLayout(sb_volume_layout)
        
        monitor_volume_layout = self._create_volume_slider("Volume Escutar 👂:", self.monitor_level, self.update_monitor_volume)
        self.config_layout.addLayout(monitor_volume_layout)
        
    def _create_device_combo(self, device_list, label_text):
        """Cria e preenche um QComboBox para dispositivos de áudio, incluindo SR (Taxa de Amostragem)."""
        box = QtWidgets.QWidget()
        h_layout = QtWidgets.QHBoxLayout(box)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(15) # Espaçamento fixo
        
        label = QtWidgets.QLabel(label_text)
        label.setFixedWidth(250) # Aumentado para acomodar o texto
        h_layout.addWidget(label)
        
        combo = QtWidgets.QComboBox()
        combo.setStyleSheet(f"background:#222; color:{COLOR_TEXT_NORMAL}; padding: 5px; border-radius: 5px;")

        virtual_names = {"cable output", "cable input", "mixagem estéreo", "stereo mix", "what u hear", "vb-audio"}
        
        # Prioriza dispositivos virtuais
        for d in device_list:
            name_lower = d['name'].lower()
            if any(vn in name_lower for vn in virtual_names):
                 text = f"✨ VIRTUAL: {d['name']} (SR: {d['default_samplerate']:.0f} Hz)"
                 combo.addItem(text, d['index'])
                 
        # Adiciona dispositivos físicos
        for d in device_list:
            name_lower = d['name'].lower()
            if not any(vn in name_lower for vn in virtual_names):
                text = f"{d['name']} (SR: {d['default_samplerate']:.0f} Hz)"
                combo.addItem(text, d['index'])
        
        h_layout.addWidget(combo)
        
        return combo, box

    def _set_default_device(self, combo, default_index):
        """Tenta pré-selecionar o dispositivo padrão na ComboBox."""
        index = combo.findData(default_index)
        if index != -1:
            combo.setCurrentIndex(index)
            
    def _create_volume_slider(self, label_text, initial_value, update_method):
        """Cria um layout horizontal com label, slider e label de valor para o volume."""
        h_layout = QtWidgets.QHBoxLayout()
        h_layout.setSpacing(15) # Espaçamento fixo
        
        label = QtWidgets.QLabel(label_text)
        label.setFixedWidth(250) # MESMA LARGURA DO COMBOBOX (Tamanho Padronizado)
        h_layout.addWidget(label)
        
        slider = NoScrollSlider(QtCore.Qt.Horizontal) # USANDO O SLIDER SEM SCROLL
        slider.setRange(0, 100)
        slider.setValue(initial_value)
        slider.setSingleStep(5)
        
        value_label = QtWidgets.QLabel(f"{initial_value}%")
        value_label.setFixedWidth(50)
        
        # Conecta a atualização da label e o valor global
        slider.valueChanged.connect(lambda value, vl=value_label: vl.setText(f"{value}%"))
        slider.valueChanged.connect(update_method)

        h_layout.addWidget(slider)
        h_layout.addWidget(value_label)
        
        return h_layout

    # --- Seção 3: Soundboard Management (Customizados) ---
    def _setup_soundboard_management_section(self):
        
        self.config_layout.addWidget(self._create_header("3. Atalhos Customizados (Adicionar/Editar/Remover)"))
        
        self.custom_shortcuts_container = QtWidgets.QVBoxLayout()
        self.config_layout.addLayout(self.custom_shortcuts_container)
        self._update_custom_shortcuts_ui()
        
        btn_add_shortcut = QtWidgets.QPushButton("➕ Adicionar Novo Atalho Customizado")
        btn_add_shortcut.clicked.connect(lambda: self.add_shortcut_dialog())
        btn_add_shortcut.setStyleSheet(f"padding:10px; background:{COLOR_ACCENT_MIC}; color:black; font-weight:bold; border-radius: 8px;")
        self.config_layout.addWidget(btn_add_shortcut)
        
    def _update_custom_shortcuts_ui(self):
        """Redesenha a lista de atalhos customizados na tela de config."""
        # Limpa o layout
        while self.custom_shortcuts_container.count():
            item = self.custom_shortcuts_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
                
        # Filtra e ordena os atalhos
        custom_keys = sorted([k for k in SOUNDBOARD_SHORTCUTS.keys() if SOUNDBOARD_SHORTCUTS[k] and k != '0'], key=lambda x: (
            0 if x.startswith('home+') and x.strip('home+').isdigit() and int(x.strip('home+')) in range(1, 10) else 1,
            x.split('+')[-1]
        ))
        
        if not custom_keys:
             self.custom_shortcuts_container.addWidget(QtWidgets.QLabel("Nenhum atalho customizado adicionado."))
             return
        
        for hotkey in custom_keys:
            path = SOUNDBOARD_SHORTCUTS.get(hotkey) 
            
            h_widget = QtWidgets.QWidget()
            h_widget.setStyleSheet(f"background:#222; border-radius: 5px; border: 1px solid {COLOR_BORDER};")
            h_layout = QtWidgets.QHBoxLayout(h_widget)
            h_layout.setContentsMargins(5, 5, 5, 5)

            is_auto_key = hotkey.startswith('home+') and hotkey.strip('home+').isdigit() and int(hotkey.strip('home+')) in range(1, 10)
            
            label_text = f"**{hotkey.upper()}** — {os.path.basename(path)}" if path else f"**{hotkey.upper()}** — Nenhum áudio."
            if is_auto_key and self.soundboard_folder:
                 label_text = f"**[AUTO] {hotkey.upper()}** — {os.path.basename(path)}"
                 
            label_sb = QtWidgets.QLabel(label_text)
            label_sb.setStyleSheet("padding:5px; background:transparent; font-size:12px;")
            h_layout.addWidget(label_sb, 1)
            
            btn_edit = QtWidgets.QPushButton("✎")
            btn_edit.setToolTip("Editar Atalho/Arquivo")
            btn_edit.setStyleSheet(f"background:#444; color:{COLOR_TEXT_NORMAL}; font-weight:bold; border-radius: 5px; padding: 5px;")
            btn_edit.setFixedWidth(40)
            btn_edit.clicked.connect(lambda checked, k=hotkey, p=path: self.add_shortcut_dialog(k, p)) 
            h_layout.addWidget(btn_edit)
            
            btn_remove = QtWidgets.QPushButton("X")
            btn_remove.setToolTip("Remover Atalho")
            btn_remove.setStyleSheet(f"background:{COLOR_ERROR}; color:white; font-weight:bold; border-radius: 5px; padding: 5px;")
            btn_remove.setFixedWidth(40)
            
            if is_auto_key and self.soundboard_folder:
                btn_remove.setEnabled(False)
                btn_remove.setToolTip("Desabilitado. Desvincule a pasta para remover atalhos automáticos.")
            else:
                btn_remove.clicked.connect(lambda checked, k=hotkey: self.remove_shortcut(k))
            
            h_layout.addWidget(btn_remove)
            self.custom_shortcuts_container.addWidget(h_widget)
            
    # --- MÉTODOS DE CONTROLE ---
    
    def _map_folder_to_shortcuts(self, initial_load=False):
        """Mapeia os 9 primeiros arquivos de áudio da pasta para atalhos HOME+1 a HOME+9."""
        global SOUNDBOARD_SHORTCUTS
        
        if not self.soundboard_folder or not os.path.isdir(self.soundboard_folder):
            if not initial_load:
                self.update_status_ui("Pasta de Soundboard inválida ou não selecionada.", COLOR_WARNING)
            return

        # Lista arquivos de áudio (wav, mp3, ogg, flac)
        audio_files = []
        for f in os.listdir(self.soundboard_folder):
            if f.lower().endswith(('.wav', '.mp3', '.ogg', '.flac')):
                audio_files.append(os.path.join(self.soundboard_folder, f))
        
        audio_files.sort() # Ordena por nome para mapeamento consistente

        # Mapeia H+1 a H+9 (máximo 9 arquivos)
        for i in range(1, 10):
            hotkey = f"home+{i}"
            if i - 1 < len(audio_files):
                SOUNDBOARD_SHORTCUTS[hotkey] = audio_files[i - 1]
            # Remove o atalho automático se o arquivo foi removido/acabou
            elif hotkey in SOUNDBOARD_SHORTCUTS and hotkey.startswith('home+') and hotkey.strip('home+').isdigit():
                del SOUNDBOARD_SHORTCUTS[hotkey]

        if not initial_load:
            self.update_status_ui(f"{len(audio_files)} arquivos mapeados na pasta Soundboard.", COLOR_ACCENT_MIC)
            self._update_soundboard_ui_from_config()
            self.setup_hotkeys()
            self.save_current_config()
            
    def select_soundboard_folder(self):
        """Abre um diálogo para selecionar a pasta do Soundboard e mapeia os atalhos."""
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Áudio para Soundboard")
        if folder:
            self.soundboard_folder = folder
            self._map_folder_to_shortcuts()
            self.update_status_ui(f"Pasta Soundboard selecionada: {folder}", COLOR_ACCENT_MIC)
            
    def setup_hotkeys(self):
        """Configura todos os atalhos de teclado registrados."""
        self._unregister_hotkeys()
        
        # Atalho mestre para parar música/soundboard: HOME + END
        keyboard.add_hotkey('home+end', lambda: self.stop_all_audio())

        for hotkey, path in SOUNDBOARD_SHORTCUTS.items():
            if path:
                try:
                    # Usar lambda para garantir que o hotkey correto seja passado
                    keyboard.add_hotkey(hotkey, lambda k=hotkey: self.hotkey_signal.emit(k))
                except ValueError as e:
                    self.update_status_ui(f"ERRO Hotkey '{hotkey}': {e}", COLOR_ERROR)

    def _unregister_hotkeys(self):
        """Remove todos os hotkeys registrados para evitar duplicação."""
        keyboard.unhook_all_hotkeys()
        
    def play_soundboard_audio(self, hotkey):
        """Lida com a lógica de iniciar/parar um atalho de soundboard."""
        global current_soundboard_key, soundboard_stop_event, playing_music, stop_music_event
        path = SOUNDBOARD_SHORTCUTS.get(hotkey)

        if not path:
            self.update_status_ui(f"Atalho {hotkey.upper()} não configurado.", COLOR_WARNING)
            return
            
        if not input_stream or not output_stream or not monitor_stream:
            self.update_status_ui("Streams de áudio não iniciados. Ative-os primeiro!", COLOR_ERROR)
            return

        is_music = hotkey == '0'

        if is_music:
            self.toggle_music(hotkey)
            return

        # Lógica para Soundboard (efeitos)
        if current_soundboard_key == hotkey:
            # Parar o efeito atual se a mesma tecla for pressionada
            if soundboard_stop_event:
                soundboard_stop_event.set()
                current_soundboard_key = None
            return

        if current_soundboard_key is not None:
            self.update_status_ui(f"Aguarde o efeito '{current_soundboard_key.upper()}' terminar...", COLOR_WARNING)
            return 
            
        if playing_music:
            stop_music_event.set() # Para a música se um efeito for iniciado
            
        current_soundboard_key = hotkey
        soundboard_stop_event = threading.Event()

        threading.Thread(
            target=play_audio_thread,
            args=(path, False, self.status_signal.emit, hotkey, soundboard_stop_event),
            daemon=True
        ).start()

    def toggle_music(self, key):
        """Inicia ou para a reprodução da música principal (key='0')."""
        global playing_music, stop_music_event
        path = SOUNDBOARD_SHORTCUTS.get(key)
        
        if not path:
            self.update_status_ui("Escolha uma música principal primeiro!", COLOR_ERROR)
            return

        if playing_music:
            stop_music_event.set()
        else:
            if current_soundboard_key is not None:
                self.update_status_ui(f"Música ignorada: Soundboard ({current_soundboard_key.upper()}) está tocando.", COLOR_WARNING)
                return
                
            threading.Thread(
                target=play_audio_thread, 
                args=(path, True, self.status_signal.emit), 
                daemon=True
            ).start()
            
    def stop_all_audio(self):
        """Para a música e o soundboard simultaneamente (HOME+END)."""
        global playing_music, stop_music_event, current_soundboard_key, soundboard_stop_event
        
        stopped = False
        
        if playing_music:
            stop_music_event.set()
            stopped = True
            
        if current_soundboard_key is not None and soundboard_stop_event:
            soundboard_stop_event.set()
            current_soundboard_key = None
            stopped = True
            
        if stopped:
            self.update_status_ui("TODOS os áudios parados (HOME+END). Retornando ao modo voz...", COLOR_WARNING)
        
    def add_shortcut_dialog(self, hotkey=None, path=None):
        """Abre um diálogo para adicionar/editar um atalho de soundboard."""
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Adicionar/Editar Atalho Soundboard")
        dialog.setFixedWidth(400)
        dialog.setStyleSheet(f"background:{COLOR_BACKGROUND}; color:{COLOR_TEXT_NORMAL};")
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # 1. Campo de Atalho (Input)
        layout.addWidget(QtWidgets.QLabel("Atalho de Teclado (Ex: home+k, home+f2):"))
        self.hotkey_input = QtWidgets.QLineEdit(hotkey if hotkey and hotkey != '0' else '')
        self.hotkey_input.setPlaceholderText("Pressione as teclas aqui (Ex: home+j)")
        self.hotkey_input.setStyleSheet("padding: 8px; background: #333; border: 1px solid #555; border-radius: 5px;")
        
        # Bloqueia a edição manual e captura a tecla
        self.hotkey_input.setReadOnly(True)
        self.hotkey_input.mousePressEvent = lambda event: self._capture_hotkey()
        
        layout.addWidget(self.hotkey_input)
        
        # 2. Campo de Arquivo de Áudio
        layout.addWidget(QtWidgets.QLabel("\nArquivo de Áudio (.mp3, .wav, etc.):"))
        self.file_path_input = QtWidgets.QLineEdit(path or '')
        self.file_path_input.setStyleSheet("padding: 8px; background: #333; border: 1px solid #555; border-radius: 5px;")
        self.file_path_input.setReadOnly(True)

        btn_select_file = QtWidgets.QPushButton("Selecionar Arquivo...")
        btn_select_file.clicked.connect(self._select_audio_file)
        btn_select_file.setStyleSheet(f"padding: 8px; background:{COLOR_ACCENT_AUDIO}; color:black; font-weight: bold; border-radius: 5px;")
        layout.addWidget(btn_select_file)
        
        # 3. Botão Salvar
        btn_save = QtWidgets.QPushButton("Salvar Atalho")
        btn_save.clicked.connect(lambda: self._save_shortcut(dialog, self.hotkey_input.text(), self.file_path_input.text(), hotkey))
        btn_save.setStyleSheet(f"padding: 10px; margin-top: 15px; background:{COLOR_ACCENT_MIC}; color:black; font-weight:bold; border-radius: 8px;")
        layout.addWidget(btn_save)
        
        # 4. Botão Cancelar
        btn_cancel = QtWidgets.QPushButton("Cancelar")
        btn_cancel.clicked.connect(dialog.reject)
        btn_cancel.setStyleSheet(f"padding: 10px; background:#444; color:{COLOR_TEXT_NORMAL}; border-radius: 8px;")
        layout.addWidget(btn_cancel)

        dialog.exec_()
    
    def _capture_hotkey(self):
        """Captura o próximo atalho de teclado e o insere no QLineEdit."""
        self.hotkey_input.setText("Pressione o atalho...")
        self.hotkey_input.repaint() # Força a atualização visual
        
        # Desvincula temporariamente o atalho atual para evitar loop
        if self.current_hotkey_dialog:
            keyboard.unhook_hotkey(self.current_hotkey_dialog)
            
        def _on_key_press(event):
            # Formata o atalho
            hotkey_str = '+'.join(sorted(list(keyboard.get_hotkey_name())))
            if hotkey_str:
                self.hotkey_input.setText(hotkey_str.lower())
                keyboard.unhook_all_hotkeys() # Interrompe a captura
                self.setup_hotkeys() # Reconecta todos os atalhos
            
        # Cria um novo gancho para capturar a próxima combinação de teclas
        self.current_hotkey_dialog = keyboard.hook(_on_key_press)
        
    def _select_audio_file(self):
        """Abre o diálogo para selecionar o arquivo de áudio."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 
            "Selecionar Áudio", 
            self.soundboard_folder or "", 
            "Arquivos de Áudio (*.mp3 *.wav *.ogg *.flac)"
        )
        if path:
            self.file_path_input.setText(path)
            
    def _save_shortcut(self, dialog, hotkey_to_save, path, previous_hotkey=None):
        """Salva o atalho no dicionário global e na configuração."""
        global SOUNDBOARD_SHORTCUTS
        
        if not hotkey_to_save or not path:
            self.update_status_ui("Atalho e caminho do arquivo são obrigatórios!", COLOR_ERROR)
            return

        # 1. Remove atalho anterior, se estiver sendo editado ou renomeado
        if previous_hotkey and previous_hotkey != hotkey_to_save and previous_hotkey in SOUNDBOARD_SHORTCUTS:
            del SOUNDBOARD_SHORTCUTS[previous_hotkey]
            
        # 2. Remove o atalho se o hotkey já existir com outro arquivo
        if hotkey_to_save in SOUNDBOARD_SHORTCUTS and SOUNDBOARD_SHORTCUTS[hotkey_to_save] != path:
             # Se for um atalho automático de pasta (H+1 a H+9), o mapeamento automático será desvinculado
            is_auto_key = hotkey_to_save.startswith('home+') and hotkey_to_save.strip('home+').isdigit() and int(hotkey_to_save.strip('home+')) in range(1, 10)
            if is_auto_key and self.soundboard_folder:
                self.soundboard_folder = ''
                self.update_status_ui("A pasta de Soundboard foi desvinculada para manter sua edição manual.", COLOR_WARNING)
            
        # 3. Salva o novo atalho
        SOUNDBOARD_SHORTCUTS[hotkey_to_save] = path
            
        self.update_status_ui(f"Atalho {hotkey_to_save} salvo. Reiniciando hotkeys...", COLOR_ACCENT_MIC)
        self._update_soundboard_ui_from_config()
        self._update_custom_shortcuts_ui() # Atualiza a lista na aba de Configurações
        self.setup_hotkeys()
        self.save_current_config()
        
        dialog.accept()

    def remove_shortcut(self, hotkey):
        global SOUNDBOARD_SHORTCUTS
        if hotkey in SOUNDBOARD_SHORTCUTS:
            
            # Se for um atalho automático (H+1 a H+9), o mapeamento de pasta é quebrado
            is_auto_key = hotkey.startswith('home+') and hotkey.strip('home+').isdigit() and int(hotkey.strip('home+')) in range(1, 10)
            if is_auto_key and self.soundboard_folder:
                self.soundboard_folder = ''
                self.update_status_ui("A pasta de Soundboard foi desvinculada para permitir a remoção de atalhos.", COLOR_WARNING)
                
            del SOUNDBOARD_SHORTCUTS[hotkey]
            
            # Se a pasta não foi desvinculada, tenta remapear após remover
            if self.soundboard_folder:
                self._map_folder_to_shortcuts()
                
            self.update_status_ui(f"Atalho {hotkey} removido. Reiniciando hotkeys...", COLOR_WARNING)
            self._update_soundboard_ui_from_config()
            self._update_custom_shortcuts_ui() # Atualiza a lista na aba de Configurações
            self.setup_hotkeys()
            self.save_current_config()
            
    # --- Atualizações de Volume e UI ---

    def update_music_volume(self, value):
        global music_volume_factor
        self.volume_level = value
        music_volume_factor = value / 100.0
        
    def update_mic_volume(self, value):
        global mic_volume_factor
        self.mic_level = value
        mic_volume_factor = value / 100.0
        
    def update_monitor_volume(self, value):
        global monitor_volume_factor
        self.monitor_level = value
        monitor_volume_factor = value / 100.0

    @QtCore.pyqtSlot(str, str)
    def update_status_ui(self, message, color):
        """Atualiza a label de status na thread da UI."""
        self.status.setText(f"Status: {message}")
        self.status.setStyleSheet(f"color:{color}; font-size:14px; padding:10px; background:#222; border-radius: 8px; margin-top: 15px;")

    def _clear_soundboard_container(self):
        """Limpa o layout dinâmico do Soundboard (QGridLayout)."""
        while self.soundboard_grid_layout.count():
            item = self.soundboard_grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
    
    def _update_soundboard_ui_from_config(self):
        """Redesenha a seção do Soundboard com base em SOUNDBOARD_SHORTCUTS usando QGridLayout."""
        global SOUNDBOARD_SHORTCUTS 
        self._clear_soundboard_container()
        
        is_active = input_stream is not None and output_stream is not None and monitor_stream is not None
        
        # 1. Atualiza o botão de música principal (HOME + 0)
        if self.musica_path and is_active:
            self.btn_play.setEnabled(True)
            self.btn_play.setText(f"🎵 Tocar/Parar Música: {os.path.basename(self.musica_path)} (HOME + 0)")
        else:
            self.btn_play.setEnabled(False)
            self.btn_play.setText("🎵 Tocar/Parar Música (Áudio não configurado)")
            
        # 2. Ordenação para Soundboard
        sorted_keys = sorted([k for k in SOUNDBOARD_SHORTCUTS.keys() if SOUNDBOARD_SHORTCUTS[k] and k != '0'], key=lambda x: (
            0 if x.startswith('home+') and x.strip('home+').isdigit() and int(x.strip('home+')) in range(1, 10) else 1,
            x.split('+')[-1]
        ))
        
        COLUMNS = 5 
        
        for index, hotkey in enumerate(sorted_keys):
            path = SOUNDBOARD_SHORTCUTS.get(hotkey) 
            
            btn_play_sb = QtWidgets.QPushButton()
            file_name = os.path.basename(path)
            
            btn_play_sb.setText(f"[{hotkey.upper()}]\n{file_name[:20]}{'...' if len(file_name) > 20 else ''}")
            
            btn_play_sb.setToolTip(f"Tocar/Parar | {hotkey.upper()} - {path}")
            
            # Adiciona o ícone padrão
            if os.path.exists(ICON_PATH):
                btn_play_sb.setIcon(QtGui.QIcon(ICON_PATH))
                
            btn_play_sb.setStyleSheet(f"""
                QPushButton {{
                    background:#222; 
                    color:{COLOR_TEXT_NORMAL}; 
                    padding: 5px; 
                    font-weight: bold; 
                    border-radius: 8px;
                    border: 1px solid {COLOR_ACCENT_MIC};
                    min-height: 70px;
                    max-height: 70px;
                    text-align: center;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background: {COLOR_ACCENT_MIC};
                    color: black;
                }}
            """)
            
            btn_play_sb.clicked.connect(lambda checked, k=hotkey: self.play_soundboard_audio(k))
            btn_play_sb.setEnabled(is_active)
            
            row = index // COLUMNS
            col = index % COLUMNS
            
            self.soundboard_grid_layout.addWidget(btn_play_sb, row, col)
            
        SOUNDBOARD_SHORTCUTS = {k: v for k, v in SOUNDBOARD_SHORTCUTS.items() if v or k == '0'}
        
    def _update_start_stop_ui(self):
        """Atualiza o estado visual dos botões Start/Stop."""
        global input_stream, output_stream, monitor_stream
        is_active = input_stream is not None and output_stream is not None and monitor_stream is not None
        
        input_sr = self.get_input_samplerate()
        output_sr = self.get_output_samplerate()
        
        if is_active:
            text = "PARAR AUDIO STREAMS (Desativar)"
            style = f"padding:15px; background:{COLOR_ERROR}; color:white; font-weight:bold; font-size:18px; border-radius: 10px; margin-bottom: 15px;"
            status_text = f"Streams ATIVOS (IN: {input_sr:.0f} | OUT: {output_sr:.0f} Hz). Monitoramento Condicional OK."
            status_color = COLOR_ACCENT_MIC
            self.start_stop_action.setText("Parar Streams") # Para o menu da bandeja
        else:
            text = "INICIAR AUDIO STREAMS (Ativar)"
            style = f"padding:15px; background:{COLOR_ACCENT_MIC}; color:black; font-weight:bold; font-size:18px; border-radius: 10px; margin-bottom: 15px;"
            status_text = "Streams INATIVOS. Pressione 'INICIAR AUDIO STREAMS'."
            status_color = COLOR_WARNING
            self.start_stop_action.setText("Iniciar Streams") # Para o menu da bandeja

        self.btn_start_stop.setText(text)
        self.btn_start_stop.setStyleSheet(style)
        self.update_status_ui(status_text, status_color)
        
        self._update_soundboard_ui_from_config()
        
    def start_streams(self):
        """Inicia os streams de áudio do microfone real, saída virtual e monitoramento usando a taxa de amostragem correta."""
        global input_stream, output_stream, monitor_stream
        
        input_device_index = self.config.get('input_device_index', -1)
        output_device_index = self.config.get('output_device_index', -1)
        monitor_device_index = self.config.get('monitor_device_index', -1)
        
        if input_device_index == -1 or output_device_index == -1 or monitor_device_index == -1:
             self.update_status_ui("ERRO: Configure Microfone Real, Saída Virtual e Saída Monitor primeiro (⚙️).", COLOR_ERROR)
             return
             
        input_sr = self.get_input_samplerate()
        output_sr = self.get_output_samplerate()
        monitor_sr = self.get_monitor_samplerate()

        try:
            # Tenta iniciar com a taxa padrão do dispositivo. Se falhar, PortAudio irá tentar a default.
            input_stream = sd.InputStream(device=input_device_index, channels=CHANNELS, samplerate=input_sr, blocksize=BLOCKSIZE, callback=input_callback)
            output_stream = sd.OutputStream(device=output_device_index, channels=CHANNELS, samplerate=output_sr, blocksize=BLOCKSIZE, callback=output_callback)
            monitor_stream = sd.OutputStream(device=monitor_device_index, channels=CHANNELS, samplerate=monitor_sr, blocksize=BLOCKSIZE, callback=monitor_callback)

            input_stream.start()
            output_stream.start()
            monitor_stream.start() # Inicia o monitoramento, o callback lida com a lógica de ativação/desativação

            self._update_start_stop_ui()
            self.save_current_config()

        except Exception as e:
            # O erro PaErrorCode -9997 (Invalid Sample Rate) é capturado aqui.
            self.update_status_ui(f"ERRO ao iniciar streams: {e}. Verifique as taxas de amostragem na aba ⚙️. (Erro PA: {e.args[0] if e.args else ''})", COLOR_ERROR)
            print(f"ERRO: {e}", file=sys.stderr)
            self.stop_streams() # Garante que todos os streams sejam fechados em caso de falha

    def stop_streams(self):
        """Para todos os streams de áudio."""
        global input_stream, output_stream, monitor_stream, playing_music, mode_voice
        
        self.stop_all_audio() # Garante que todo áudio de soundboard/música pare

        if input_stream:
            input_stream.stop()
            input_stream.close()
            input_stream = None
        if output_stream:
            output_stream.stop()
            output_stream.close()
            output_stream = None
        if monitor_stream:
            monitor_stream.stop()
            monitor_stream.close()
            monitor_stream = None
            
        mode_voice = True
        playing_music = False

        while not output_queue.empty():
            try: output_queue.get_nowait()
            except: pass
            
        self._update_start_stop_ui()

    def toggle_streams(self):
        """Alterna entre iniciar e parar os streams."""
        global input_stream, output_stream
        if input_stream is None or output_stream is None:
            self.start_streams()
        else:
            self.stop_streams()

    def update_monitor_stream_state(self):
        """Controla a ativação/desativação do stream de monitoramento."""
        global monitor_stream
        if monitor_stream is None: return
        
        is_playing = playing_music or current_soundboard_key is not None
        
        if is_playing and monitor_stream.stopped:
            # Se for tocar som, o monitoramento deve estar ativo para ouvir o soundboard
            monitor_stream.start() 
        elif not is_playing and not mode_voice and monitor_stream.stopped:
            # Se a voz estiver pausada e não houver som, não precisa de monitoramento
            pass 
        elif mode_voice and monitor_stream.stopped:
            # Se a voz estiver ativa, ligue o monitoramento
            monitor_stream.start()
        
    def setup_tray_icon(self):
        """Configura o ícone da bandeja do sistema (System Tray)."""
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        
        # Tenta usar o logo
        if os.path.exists(ICON_PATH):
            icon = QtGui.QIcon(ICON_PATH)
        else:
            icon = self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)

        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("VoiceGaming SWITCH")
        
        self.menu = QtWidgets.QMenu()
        
        self.start_stop_action = QtWidgets.QAction("Iniciar Streams", self)
        self.start_stop_action.triggered.connect(self.toggle_streams)
        self.menu.addAction(self.start_stop_action)
        
        config_action = QtWidgets.QAction("Configurações", self)
        config_action.triggered.connect(lambda: self.tab_widget.setCurrentIndex(1) or self.showNormal())
        self.menu.addAction(config_action)
        
        quit_action = QtWidgets.QAction("Sair", self)
        quit_action.triggered.connect(QtWidgets.QApplication.instance().quit)
        self.menu.addAction(quit_action)

        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)
        self.tray_icon.show()

    def on_tray_icon_activated(self, reason):
        """Gerencia o clique no ícone da bandeja."""
        if reason == QtWidgets.QSystemTrayIcon.Trigger: # Clique simples
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()

    def closeEvent(self, event):
        """Comportamento ao fechar a janela principal."""
        self.hide()
        event.ignore()
        
# ==================== INICIALIZAÇÃO ====================

if __name__ == "__main__":
    keyboard.unhook_all()
    
    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
    else:
        app = QtWidgets.QApplication.instance()
        
    app.setQuitOnLastWindowClosed(False) 
    
    # Define o ícone da aplicação
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QtGui.QIcon(ICON_PATH))
    
    font = QtGui.QFont("Segoe UI", 10)
    app.setFont(font)
    
    window = VoiceGamingSWITCH()
    window.show()
    sys.exit(app.exec_())