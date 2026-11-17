import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import models, transforms
from torch.cuda.amp import autocast, GradScaler
from torch.utils.data import Dataset, DataLoader
from safetensors.torch import save_file, load_file
import logging
import os
from pathlib import Path
import numpy as np
from sklearn.metrics import accuracy_score, f1_score, classification_report
from typing import Optional, Dict, List, Tuple
import wandb
from tqdm import tqdm

# ═══════════════════════════════════════════════════════════════════════════════
# 1. إعدادات النظام والتسجيل (Logging & Config)
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('training.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ModelConfig:
    """إعدادات شاملة للنموذج"""
    def __init__(self):
        # معاملات النموذج الأساسية
        self.vocab_size = 10000
        self.embed_dim = 256
        self.hidden_dim = 512
        self.num_classes = 3
        self.dropout = 0.3
        
        # معاملات التدريب
        self.lr = 1e-3
        self.batch_size = 32
        self.num_epochs = 50
        self.gradient_accumulation_steps = 4
        self.max_grad_norm = 1.0
        
        # معاملات Early Stopping
        self.patience = 7
        self.min_delta = 0.001
        
        # معاملات EMA
        self.use_ema = True
        self.ema_decay = 0.999
        
        # معاملات Label Smoothing
        self.label_smoothing = 0.1
        
        # معاملات النص
        self.max_seq_length = 128
        self.num_lstm_layers = 2
        self.lstm_bidirectional = True
        
        # معاملات Cross-Attention
        self.num_attention_heads = 4
        
        # إعدادات الجهاز
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.use_amp = torch.cuda.is_available()
        self.num_workers = 4
        
        # مسارات الحفظ
        self.checkpoint_dir = Path("checkpoints")
        self.checkpoint_dir.mkdir(exist_ok=True)
        
        # Weights & Biases
        self.use_wandb = False  # غيّرها إلى True لتفعيل W&B
        self.wandb_project = "ultimate-bitnet"
        
config = ModelConfig()

# ═══════════════════════════════════════════════════════════════════════════════
# 2. طبقة BitLinear المحسّنة (Optimized BitLinear)
# ═══════════════════════════════════════════════════════════════════════════════

class BitLinear(nn.Linear):
    """
    طبقة خطية محسنة تستخدم تكميم 1-bit للأوزان لتقليل استهلاك الذاكرة وتسريع الحسابات.
    مع إضافة تحسينات على الاستقرار والأداء.
    """
    def __init__(self, in_features, out_features, bias=True, quantize_activations=False):
        super().__init__(in_features, out_features, bias)
        self.quantize_activations = quantize_activations
        
    def forward(self, x):
        w = self.weight
        
        # تطبيع الأوزان (Weight Centering) مع معالجة الحالات الخاصة
        w_mean = w.mean()
        w = w - w_mean
        
        # التكميم إلى {-1, 1} مع قياس الحجم (Scaling Factor)
        gamma = w.abs().mean().clamp(min=1e-5)  # منع القسمة على صفر
        w_quant = torch.sign(w) * gamma
        
        # Straight-Through Estimator (STE)
        w_final = w + (w_quant - w).detach()
        
        # تكميم المدخلات (اختياري)
        if self.quantize_activations and self.training:
            x_abs_max = x.abs().amax(dim=-1, keepdim=True).clamp(min=1e-5)
            scale_x = 127.0 / x_abs_max
            x_quant = (x * scale_x).round().clamp(-128, 127) / scale_x
            x_final = x + (x_quant - x).detach()
        else:
            x_final = x
        
        return F.linear(x_final, w_final, self.bias)

# ═══════════════════════════════════════════════════════════════════════════════
# 3. مكونات مساعدة (Helper Components)
# ═══════════════════════════════════════════════════════════════════════════════

class MetricsTracker:
    """تتبع وحساب المقاييس أثناء التدريب والتقييم"""
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.predictions = []
        self.targets = []
        self.losses = []
    
    def update(self, preds: torch.Tensor, targets: torch.Tensor, loss: float):
        """تحديث المقاييس بدفعة جديدة"""
        self.predictions.extend(preds.cpu().numpy())
        self.targets.extend(targets.cpu().numpy())
        self.losses.append(loss)
    
    def compute(self) -> Dict[str, float]:
        """حساب جميع المقاييس"""
        if len(self.predictions) == 0:
            return {'loss': 0.0, 'accuracy': 0.0, 'f1_macro': 0.0, 'f1_weighted': 0.0}
        
        return {
            'loss': np.mean(self.losses),
            'accuracy': accuracy_score(self.targets, self.predictions),
            'f1_macro': f1_score(self.targets, self.predictions, average='macro', zero_division=0),
            'f1_weighted': f1_score(self.targets, self.predictions, average='weighted', zero_division=0)
        }
    
    def get_report(self) -> str:
        """الحصول على تقرير تفصيلي"""
        if len(self.predictions) == 0:
            return "No predictions yet"
        return classification_report(self.targets, self.predictions, zero_division=0)

class EarlyStopping:
    """إيقاف مبكر للتدريب عند توقف التحسن"""
    def __init__(self, patience: int = 7, min_delta: float = 0.001, mode: str = 'min'):
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        self.best_model_state = None
        
    def __call__(self, score: float, model: nn.Module) -> bool:
        """
        تحديث حالة Early Stopping
        Returns: True إذا يجب التوقف
        """
        if self.best_score is None:
            self.best_score = score
            self.save_checkpoint(model)
            return False
        
        if self._is_improvement(score):
            self.best_score = score
            self.counter = 0
            self.save_checkpoint(model)
            logger.info(f'✓ تحسن! Best score: {self.best_score:.4f}')
            return False
        else:
            self.counter += 1
            logger.info(f'⚠ لا يوجد تحسن. EarlyStopping counter: {self.counter}/{self.patience}')
            if self.counter >= self.patience:
                self.early_stop = True
                logger.info('🛑 Early stopping triggered!')
                return True
            return False
    
    def _is_improvement(self, score: float) -> bool:
        """التحقق من وجود تحسن"""
        if self.mode == 'min':
            return score < self.best_score - self.min_delta
        else:
            return score > self.best_score + self.min_delta
    
    def save_checkpoint(self, model: nn.Module):
        """حفظ أفضل نموذج"""
        self.best_model_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
    
    def load_best_model(self, model: nn.Module):
        """تحميل أفضل نموذج"""
        if self.best_model_state is not None:
            model.load_state_dict({k: v.to(config.device) for k, v in self.best_model_state.items()})
            logger.info('✓ تم تحميل أفضل نموذج')

class EMA:
    """Exponential Moving Average للأوزان - يحسن الاستقرار والأداء"""
    def __init__(self, model: nn.Module, decay: float = 0.999):
        self.model = model
        self.decay = decay
        self.shadow = {}
        self.backup = {}
        
        # تهيئة الظلال
        for name, param in model.named_parameters():
            if param.requires_grad:
                self.shadow[name] = param.data.clone()
    
    def update(self):
        """تحديث المتوسطات المتحركة"""
        for name, param in self.model.named_parameters():
            if param.requires_grad:
                new_average = (1.0 - self.decay) * param.data + self.decay * self.shadow[name]
                self.shadow[name] = new_average.clone()
    
    def apply_shadow(self):
        """تطبيق الأوزان المتوسطة"""
        for name, param in self.model.named_parameters():
            if param.requires_grad:
                self.backup[name] = param.data.clone()
                param.data = self.shadow[name].clone()
    
    def restore(self):
        """استعادة الأوزان الأصلية"""
        for name, param in self.model.named_parameters():
            if param.requires_grad:
                param.data = self.backup[name].clone()
        self.backup = {}

class LabelSmoothingCrossEntropy(nn.Module):
    """Cross Entropy مع Label Smoothing لتحسين التعميم"""
    def __init__(self, epsilon: float = 0.1, reduction: str = 'mean'):
        super().__init__()
        self.epsilon = epsilon
        self.reduction = reduction
    
    def forward(self, preds: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        n_classes = preds.size(-1)
        log_preds = F.log_softmax(preds, dim=-1)
        
        # حساب الخسارة مع التنعيم
        with torch.no_grad():
            true_dist = torch.zeros_like(log_preds)
            true_dist.fill_(self.epsilon / (n_classes - 1))
            true_dist.scatter_(1, targets.unsqueeze(1), 1.0 - self.epsilon)
        
        loss = torch.sum(-true_dist * log_preds, dim=-1)
        
        if self.reduction == 'mean':
            return loss.mean()
        elif self.reduction == 'sum':
            return loss.sum()
        return loss

# ═══════════════════════════════════════════════════════════════════════════════
# 4. Text Encoder المحسّن مع LSTM و Attention
# ═══════════════════════════════════════════════════════════════════════════════

class TextEncoder(nn.Module):
    """
    معالج نصوص متقدم يستخدم:
    - LSTM ثنائي الاتجاه لفهم التسلسل
    - Attention mechanism لالتقاط الكلمات المهمة
    - BitLinear للكفاءة
    """
    def __init__(self, vocab_size: int, embed_dim: int, hidden_dim: int, 
                 num_layers: int = 2, dropout: float = 0.3):
        super().__init__()
        self.hidden_dim = hidden_dim
        
        # طبقة Embedding مع padding
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.embed_dropout = nn.Dropout(dropout)
        
        # LSTM ثنائي الاتجاه
        self.lstm = nn.LSTM(
            embed_dim, 
            hidden_dim // 2,  # لأنه bidirectional
            num_layers=num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0
        )
        
        # Attention mechanism
        self.attention = nn.Sequential(
            BitLinear(hidden_dim, hidden_dim // 2),
            nn.Tanh(),
            BitLinear(hidden_dim // 2, 1)
        )
        
        self.layer_norm = nn.LayerNorm(hidden_dim)
        self.output_dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, lengths: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Args:
            x: (batch, seq_len) - token indices
            lengths: (batch,) - actual lengths for packing
        Returns:
            (batch, hidden_dim) - encoded representation
        """
        # Embedding
        embedded = self.embedding(x)  # (batch, seq_len, embed_dim)
        embedded = self.embed_dropout(embedded)
        
        # LSTM processing مع packing اختياري
        if lengths is not None:
            # ترتيب حسب الطول للـ packing
            lengths_clamped = lengths.clamp(min=1, max=x.size(1))
            packed = nn.utils.rnn.pack_padded_sequence(
                embedded, lengths_clamped.cpu(), batch_first=True, enforce_sorted=False
            )
            lstm_out, _ = self.lstm(packed)
            lstm_out, _ = nn.utils.rnn.pad_packed_sequence(lstm_out, batch_first=True)
        else:
            lstm_out, _ = self.lstm(embedded)  # (batch, seq_len, hidden_dim)
        
        # Attention pooling
        attn_weights = self.attention(lstm_out)  # (batch, seq_len, 1)
        attn_weights = F.softmax(attn_weights, dim=1)
        
        # Weighted sum
        context = torch.sum(attn_weights * lstm_out, dim=1)  # (batch, hidden_dim)
        
        # Normalization و Dropout
        output = self.layer_norm(context)
        output = self.output_dropout(output)
        
        return output

# ═══════════════════════════════════════════════════════════════════════════════
# 5. Image Encoder المحسّن
# ═══════════════════════════════════════════════════════════════════════════════

class ImageEncoder(nn.Module):
    """
    معالج صور محسّن مع:
    - MobileNetV3 للكفاءة
    - Fine-tuning اختياري
    - BitLinear projection
    """
    def __init__(self, output_dim: int, fine_tune: bool = False):
        super().__init__()
        
        # تحميل MobileNetV3-Small
        weights = models.MobileNet_V3_Small_Weights.DEFAULT
        base_model = models.mobilenet_v3_small(weights=weights)
        
        # استخراج الميزات
        self.features = base_model.features
        self.avgpool = base_model.avgpool
        
        # Fine-tuning: فك تجميد الطبقات الأخيرة فقط
        if fine_tune:
            # تجميد جميع الطبقات أولاً
            for param in self.features.parameters():
                param.requires_grad = False
            # فك تجميد آخر 3 طبقات
            for param in self.features[-3:].parameters():
                param.requires_grad = True
            logger.info("✓ Fine-tuning enabled for last 3 layers of MobileNet")
        else:
            for param in self.features.parameters():
                param.requires_grad = False
        
        # Projection head محسّن
        self.project = nn.Sequential(
            BitLinear(576, output_dim * 2),
            nn.BatchNorm1d(output_dim * 2),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
            BitLinear(output_dim * 2, output_dim),
            nn.LayerNorm(output_dim)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (batch, 3, 224, 224) - images
        Returns:
            (batch, output_dim) - encoded features
        """
        x = self.features(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        return self.project(x)

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Cross-Modal Attention للدمج الذكي
# ═══════════════════════════════════════════════════════════════════════════════

class CrossModalAttention(nn.Module):
    """
    دمج متقدم بين الصورة والنص باستخدام Multi-Head Attention
    يسمح للنص بالانتباه للصورة والعكس
    """
    def __init__(self, hidden_dim: int, num_heads: int = 4, dropout: float = 0.1):
        super().__init__()
        assert hidden_dim % num_heads == 0, "hidden_dim must be divisible by num_heads"
        
        self.num_heads = num_heads
        self.hidden_dim = hidden_dim
        
        # Text-to-Image attention
        self.text_to_img_attn = nn.MultiheadAttention(
            hidden_dim, num_heads, dropout=dropout, batch_first=True
        )
        
        # Image-to-Text attention
        self.img_to_text_attn = nn.MultiheadAttention(
            hidden_dim, num_heads, dropout=dropout, batch_first=True
        )
        
        # Feed-forward networks
        self.ffn_text = nn.Sequential(
            BitLinear(hidden_dim, hidden_dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            BitLinear(hidden_dim * 2, hidden_dim)
        )
        
        self.ffn_img = nn.Sequential(
            BitLinear(hidden_dim, hidden_dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            BitLinear(hidden_dim * 2, hidden_dim)
        )
        
        # Layer normalization
        self.norm1_text = nn.LayerNorm(hidden_dim)
        self.norm2_text = nn.LayerNorm(hidden_dim)
        self.norm1_img = nn.LayerNorm(hidden_dim)
        self.norm2_img = nn.LayerNorm(hidden_dim)
        
        # Fusion layer
        self.fusion = nn.Sequential(
            BitLinear(hidden_dim * 2, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout)
        )
    
    def forward(self, text_feat: torch.Tensor, img_feat: torch.Tensor) -> torch.Tensor:
        """
        Args:
            text_feat: (batch, hidden_dim)
            img_feat: (batch, hidden_dim)
        Returns:
            (batch, hidden_dim) - fused features
        """
        # إضافة بُعد sequence length
        text_feat = text_feat.unsqueeze(1)  # (batch, 1, hidden_dim)
        img_feat = img_feat.unsqueeze(1)    # (batch, 1, hidden_dim)
        
        # Text attends to Image
        text_attn, _ = self.text_to_img_attn(text_feat, img_feat, img_feat)
        text_feat = self.norm1_text(text_feat + text_attn)
        text_ffn = self.ffn_text(text_feat)
        text_enhanced = self.norm2_text(text_feat + text_ffn)
        
        # Image attends to Text
        img_attn, _ = self.img_to_text_attn(img_feat, text_feat, text_feat)
        img_feat = self.norm1_img(img_feat + img_attn)
        img_ffn = self.ffn_img(img_feat)
        img_enhanced = self.norm2_img(img_feat + img_ffn)
        
        # إزالة بُعد sequence و دمج
        text_enhanced = text_enhanced.squeeze(1)
        img_enhanced = img_enhanced.squeeze(1)
        
        combined = torch.cat([text_enhanced, img_enhanced], dim=-1)
        fused = self.fusion(combined)
        
        return fused

# ═══════════════════════════════════════════════════════════════════════════════
# 7. النموذج النهائي المحسّن
# ═══════════════════════════════════════════════════════════════════════════════

class UltimateBitNet(nn.Module):
    """
    النموذج الكامل المحسّن مع جميع التحسينات:
    - Text Encoder متقدم مع LSTM + Attention
    - Image Encoder محسّن مع MobileNetV3
    - Cross-Modal Attention للدمج
    - BitLinear للكفاءة
    - معالجة أخطاء محسّنة
    """
    def __init__(self, cfg: ModelConfig):
        super().__init__()
        self.cfg = cfg
        
        # المشفرات
        self.text_enc = TextEncoder(
            vocab_size=cfg.vocab_size,
            embed_dim=cfg.embed_dim,
            hidden_dim=cfg.hidden_dim,
            num_layers=cfg.num_lstm_layers,
            dropout=cfg.dropout
        )
        
        self.img_enc = ImageEncoder(
            output_dim=cfg.hidden_dim,
            fine_tune=False  # يمكن تفعيله لاحقاً
        )
        
        # Cross-Modal Fusion
        self.cross_modal_attn = CrossModalAttention(
            hidden_dim=cfg.hidden_dim,
            num_heads=cfg.num_attention_heads,
            dropout=cfg.dropout
        )
        
        # طبقة التصنيف النهائية
        self.classifier = nn.Sequential(
            BitLinear(cfg.hidden_dim, cfg.hidden_dim // 2),
            nn.LayerNorm(cfg.hidden_dim // 2),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
            BitLinear(cfg.hidden_dim // 2, cfg.hidden_dim // 4),
            nn.LayerNorm(cfg.hidden_dim // 4),
            nn.GELU(),
            nn.Dropout(cfg.dropout / 2),
            BitLinear(cfg.hidden_dim // 4, cfg.num_classes)
        )
        
        # تهيئة الأوزان
        self._initialize_weights()
    
    def _initialize_weights(self):
        """تهيئة محسّنة للأوزان"""
        for m in self.modules():
            if isinstance(m, (nn.Linear, BitLinear)):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.LayerNorm):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
    
    def forward(self, text: torch.Tensor, image: torch.Tensor, 
                text_lengths: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        Args:
            text: (batch, seq_len) - token indices
            image: (batch, 3, 224, 224) - images
            text_lengths: (batch,) - actual text lengths
        Returns:
            (batch, num_classes) - logits
        """
        # التحقق من صحة المدخلات
        if torch.isnan(image).any() or torch.isnan(text).any():
            logger.error("⚠ تم اكتشاف NaN في المدخلات!")
            raise ValueError("Input contains NaN values")
        
        if torch.isinf(image).any() or torch.isinf(text).any():
            logger.error("⚠ تم اكتشاف Inf في المدخلات!")
            raise ValueError("Input contains Inf values")
        
        # معالجة النص والصورة
        t_feat = self.text_enc(text, text_lengths)
        i_feat = self.img_enc(image)
        
        # الدمج عبر Cross-Modal Attention
        fused = self.cross_modal_attn(t_feat, i_feat)
        
        # التصنيف
        logits = self.classifier(fused)
        
        return logits

# ═══════════════════════════════════════════════════════════════════════════════
# 8. Data Augmentation
# ═══════════════════════════════════════════════════════════════════════════════

def get_transforms(is_training: bool = True) -> transforms.Compose:
    """
    تحويلات الصور مع Data Augmentation قوي للتدريب
    """
    if is_training:
        return transforms.Compose([
            transforms.RandomResizedCrop(224, scale=(0.7, 1.0)),
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomRotation(15),
            transforms.ColorJitter(
                brightness=0.3,
                contrast=0.3,
                saturation=0.3,
                hue=0.1
            ),
            transforms.RandomAffine(degrees=0, translate=(0.1, 0.1)),
            transforms.RandomGrayscale(p=0.1),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406], 
                std=[0.229, 0.224, 0.225]
            ),
            transforms.RandomErasing(p=0.1)
        ])
    else:
        return transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406], 
                std=[0.229, 0.224, 0.225]
            )
        ])

# ═══════════════════════════════════════════════════════════════════════════════
# 9. حلقة التدريب والتقييم المحسّنة
# ═══════════════════════════════════════════════════════════════════════════════

def train_epoch(model: nn.Module, 
                dataloader: DataLoader, 
                optimizer: optim.Optimizer,
                criterion: nn.Module,
                scaler: GradScaler,
                scheduler: optim.lr_scheduler._LRScheduler,
                ema: Optional[EMA],
                epoch_idx: int,
                cfg: ModelConfig) -> Dict[str, float]:
    """
    حلقة تدريب محسّنة مع:
    - Gradient Accumulation
    - Mixed Precision
    - EMA
    - Progress Bar
    """
    model.train()
    metrics = MetricsTracker()
    
    pbar = tqdm(dataloader, desc=f'Training Epoch {epoch_idx}')
    optimizer.zero_grad(set_to_none=True)
    
    for batch_idx, (text, img, labels) in enumerate(pbar):
        text = text.to(cfg.device)
        img = img.to(cfg.device)
        labels = labels.to(cfg.device)
        
        # حساب أطوال النصوص (لو متوفرة)
        text_lengths = (text != 0).sum(dim=1)
        
        # Forward pass مع AMP
        with autocast(enabled=cfg.use_amp):
            outputs = model(text, img, text_lengths)
            loss = criterion(outputs, labels)
            loss = loss / cfg.gradient_accumulation_steps
        
        # Backward pass
        scaler.scale(loss).backward()
        
        # Gradient Accumulation
        if (batch_idx + 1) % cfg.gradient_accumulation_steps == 0:
            # Gradient Clipping
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=cfg.max_grad_norm)
            
            # Optimizer step
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            
            # EMA update
            if ema is not None:
                ema.update()
            
            # Scheduler step (لو OneCycleLR)
            if isinstance(scheduler, optim.lr_scheduler.OneCycleLR):
                scheduler.step()
        
        # تتبع المقاييس
        preds = outputs.argmax(dim=1)
        metrics.update(preds, labels, loss.item() * cfg.gradient_accumulation_steps)
        
        # تحديث Progress Bar
        current_lr = optimizer.param_groups[0]['lr']
        pbar.set_postfix({
            'loss': f"{loss.item() * cfg.gradient_accumulation_steps:.4f}",
            'lr': f"{current_lr:.6f}"
        })
    
    results = metrics.compute()
    
    # Scheduler step (لو ليس OneCycleLR)
    if not isinstance(scheduler, optim.lr_scheduler.OneCycleLR):
        scheduler.step()
    
    return results

def validate(model: nn.Module,
             dataloader: DataLoader,
             criterion: nn.Module,
             cfg: ModelConfig,
             ema: Optional[EMA] = None) -> Dict[str, float]:
    """
    حلقة تقييم محسّنة
    """
    # تطبيق EMA إذا كان متاحاً
    if ema is not None:
        ema.apply_shadow()
    
    model.eval()
    metrics = MetricsTracker()
    
    with torch.no_grad():
        pbar = tqdm(dataloader, desc='Validation')
        for text, img, labels in pbar:
            text = text.to(cfg.device)
            img = img.to(cfg.device)
            labels = labels.to(cfg.device)
            
            text_lengths = (text != 0).sum(dim=1)
            
            with autocast(enabled=cfg.use_amp):
                outputs = model(text, img, text_lengths)
                loss = criterion(outputs, labels)
            
            preds = outputs.argmax(dim=1)
            metrics.update(preds, labels, loss.item())
            
            pbar.set_postfix({'loss': f"{loss.item():.4f}"})
    
    # استعادة الأوزان الأصلية
    if ema is not None:
        ema.restore()
    
    results = metrics.compute()
    
    logger.info(f"📊 Validation Results:")
    logger.info(f"   Loss: {results['loss']:.4f}")
    logger.info(f"   Accuracy: {results['accuracy']:.4f}")
    logger.info(f"   F1 (Macro): {results['f1_macro']:.4f}")
    logger.info(f"   F1 (Weighted): {results['f1_weighted']:.4f}")
    
    return results

def train_model(model: nn.Module,
                train_loader: DataLoader,
                val_loader: DataLoader,
                cfg: ModelConfig) -> nn.Module:
    """
    حلقة التدريب الرئيسية الكاملة
    """
    # تهيئة المكونات
    criterion = LabelSmoothingCrossEntropy(epsilon=cfg.label_smoothing)
    
    optimizer = optim.AdamW(
        model.parameters(),
        lr=cfg.lr,
        weight_decay=1e-4,
        betas=(0.9, 0.999)
    )
    
    # OneCycleLR للتقارب السريع
    scheduler = optim.lr_scheduler.OneCycleLR(
        optimizer,
        max_lr=cfg.lr * 10,
        epochs=cfg.num_epochs,
        steps_per_epoch=len(train_loader) // cfg.gradient_accumulation_steps,
        pct_start=0.3,
        anneal_strategy='cos',
        div_factor=25.0,
        final_div_factor=1e4
    )
    
    scaler = GradScaler(enabled=cfg.use_amp)
    
    # EMA
    ema = EMA(model, decay=cfg.ema_decay) if cfg.use_ema else None
    
    # Early Stopping
    early_stopping = EarlyStopping(
        patience=cfg.patience,
        min_delta=cfg.min_delta,
        mode='min'
    )
    
    # Weights & Biases
    if cfg.use_wandb:
        wandb.init(
            project=cfg.wandb_project,
            config=vars(cfg),
            name=f"BitNet-{cfg.hidden_dim}d-{cfg.num_epochs}e"
        )
        wandb.watch(model, log='all', log_freq=100)
    
    # حلقة التدريب
    logger.info("🚀 بدء التدريب...")
    logger.info(f"   Device: {cfg.device}")
    logger.info(f"   Epochs: {cfg.num_epochs}")
    logger.info(f"   Batch Size: {cfg.batch_size}")
    logger.info(f"   Learning Rate: {cfg.lr}")
    logger.info(f"   Using AMP: {cfg.use_amp}")
    logger.info(f"   Using EMA: {cfg.use_ema}")
    
    best_val_loss = float('inf')
    
    for epoch in range(cfg.num_epochs):
        logger.info(f"\n{'='*60}")
        logger.info(f"Epoch {epoch + 1}/{cfg.num_epochs}")
        logger.info(f"{'='*60}")
        
        # Training
        train_results = train_epoch(
            model, train_loader, optimizer, criterion, 
            scaler, scheduler, ema, epoch, cfg
        )
        
        logger.info(f"📈 Training Results:")
        logger.info(f"   Loss: {train_results['loss']:.4f}")
        logger.info(f"   Accuracy: {train_results['accuracy']:.4f}")
        logger.info(f"   F1 (Macro): {train_results['f1_macro']:.4f}")
        
        # Validation
        val_results = validate(model, val_loader, criterion, cfg, ema)
        
        # W&B Logging
        if cfg.use_wandb:
            wandb.log({
                'epoch': epoch,
                'train/loss': train_results['loss'],
                'train/accuracy': train_results['accuracy'],
                'train/f1_macro': train_results['f1_macro'],
                'val/loss': val_results['loss'],
                'val/accuracy': val_results['accuracy'],
                'val/f1_macro': val_results['f1_macro'],
                'learning_rate': optimizer.param_groups[0]['lr']
            })
        
        # حفظ أفضل نموذج
        if val_results['loss'] < best_val_loss:
            best_val_loss = val_results['loss']
            save_checkpoint(model, optimizer, epoch, val_results, cfg, is_best=True)
        
        # Early Stopping
        if early_stopping(val_results['loss'], model):
            logger.info(f"🛑 التوقف المبكر عند Epoch {epoch + 1}")
            break
    
    # تحميل أفضل نموذج
    early_stopping.load_best_model(model)
    
    if cfg.use_wandb:
        wandb.finish()
    
    logger.info("✅ انتهى التدريب!")
    return model

# ═══════════════════════════════════════════════════════════════════════════════
# 10. حفظ وتحميل النماذج
# ═══════════════════════════════════════════════════════════════════════════════

def save_checkpoint(model: nn.Module,
                   optimizer: optim.Optimizer,
                   epoch: int,
                   metrics: Dict[str, float],
                   cfg: ModelConfig,
                   is_best: bool = False):
    """حفظ checkpoint كامل"""
    # التعامل مع torch.compile
    if hasattr(model, '_orig_mod'):
        state_dict = model._orig_mod.state_dict()
    else:
        state_dict = model.state_dict()
    
    checkpoint = {
        'epoch': epoch,
        'model_state_dict': state_dict,
        'optimizer_state_dict': optimizer.state_dict(),
        'metrics': metrics,
        'config': vars(cfg)
    }
    
    # حفظ آخر نموذج
    last_path = cfg.checkpoint_dir / "last_checkpoint.pth"
    torch.save(checkpoint, last_path)
    
    # حفظ أفضل نموذج
    if is_best:
        best_path = cfg.checkpoint_dir / "best_model.safetensors"
        try:
            save_file(state_dict, str(best_path))
            logger.info(f"✓ تم حفظ أفضل نموذج: {best_path}")
        except Exception as e:
            logger.warning(f"فشل حفظ SafeTensors: {e}, استخدام .pth")
            torch.save(state_dict, cfg.checkpoint_dir / "best_model.pth")

def load_checkpoint(model: nn.Module,
                   optimizer: Optional[optim.Optimizer] = None,
                   checkpoint_path: str = None,
                   cfg: ModelConfig = None) -> int:
    """تحميل checkpoint"""
    if checkpoint_path is None:
        checkpoint_path = cfg.checkpoint_dir / "last_checkpoint.pth"
    
    checkpoint = torch.load(checkpoint_path, map_location=cfg.device)
    
    model.load_state_dict(checkpoint['model_state_dict'])
    
    if optimizer is not None and 'optimizer_state_dict' in checkpoint:
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    
    epoch = checkpoint.get('epoch', 0)
    logger.info(f"✓ تم تحميل checkpoint من Epoch {epoch}")
    
    return epoch

# ═══════════════════════════════════════════════════════════════════════════════
# 11. Dataset مثال للتجربة
# ═══════════════════════════════════════════════════════════════════════════════

class DummyMultiModalDataset(Dataset):
    """Dataset وهمي للتجربة والاختبار"""
    def __init__(self, num_samples: int, vocab_size: int, max_seq_len: int, 
                 num_classes: int, transform=None):
        self.num_samples = num_samples
        self.vocab_size = vocab_size
        self.max_seq_len = max_seq_len
        self.num_classes = num_classes
        self.transform = transform or get_transforms(is_training=True)
    
    def __len__(self):
        return self.num_samples
    
    def __getitem__(self, idx):
        # نص عشوائي
        seq_len = torch.randint(10, self.max_seq_len, (1,)).item()
        text = torch.randint(1, self.vocab_size, (self.max_seq_len,))
        text[seq_len:] = 0  # padding
        
        # صورة عشوائية
        img = torch.randn(3, 224, 224)
        if self.transform:
            # تطبيع فقط للبيانات الوهمية
            img = transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225]
            )(img)
        
        # Label عشوائي
        label = torch.randint(0, self.num_classes, (1,)).item()
        
        return text, img, label

# ═══════════════════════════════════════════════════════════════════════════════
# 12. التشغيل الرئيسي
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("🚀 تهيئة النموذج المحسّن Ultimate BitNet")
    logger.info(f"   Device: {config.device}")
    
    # إنشاء النموذج
    model = UltimateBitNet(config).to(config.device)
    
    # تجميع النموذج (PyTorch 2.0+)
    try:
        model = torch.compile(model, mode='reduce-overhead')
        logger.info("✓ تم تفعيل torch.compile بنجاح")
    except Exception as e:
        logger.warning(f"⚠ torch.compile غير مدعوم: {e}")
    
    # عرض معلومات النموذج
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    logger.info(f"📊 معلومات النموذج:")
    logger.info(f"   إجمالي المعاملات: {total_params:,}")
    logger.info(f"   المعاملات القابلة للتدريب: {trainable_params:,}")
    logger.info(f"   نسبة المعاملات المجمدة: {(1 - trainable_params/total_params)*100:.2f}%")
    
    # إنشاء datasets وهمية
    train_dataset = DummyMultiModalDataset(
        num_samples=1000,
        vocab_size=config.vocab_size,
        max_seq_len=config.max_seq_length,
        num_classes=config.num_classes,
        transform=get_transforms(is_training=True)
    )
    
    val_dataset = DummyMultiModalDataset(
        num_samples=200,
        vocab_size=config.vocab_size,
        max_seq_len=config.max_seq_length,
        num_classes=config.num_classes,
        transform=get_transforms(is_training=False)
    )
    
    # DataLoaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=config.num_workers,
        pin_memory=True if torch.cuda.is_available() else False
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.num_workers,
        pin_memory=True if torch.cuda.is_available() else False
    )
    
    # بدء التدريب
    logger.info("\n" + "="*60)
    logger.info("بدء التدريب مع جميع التحسينات المتقدمة")
    logger.info("="*60 + "\n")
    
    # تدريب صغير للتجربة (3 epochs فقط)
    config.num_epochs = 3
    
    trained_model = train_model(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        cfg=config
    )
    
    # حفظ النموذج النهائي
    logger.info("\n💾 حفظ النموذج النهائي...")
    final_path = config.checkpoint_dir / "ultimate_bitnet_final.safetensors"
    try:
        if hasattr(trained_model, '_orig_mod'):
            state_dict = trained_model._orig_mod.state_dict()
        else:
            state_dict = trained_model.state_dict()
        
        save_file(state_dict, str(final_path))
        logger.info(f"✅ تم حفظ النموذج النهائي: {final_path}")
    except Exception as e:
        logger.error(f"❌ فشل الحفظ: {e}")
        torch.save(state_dict, config.checkpoint_dir / "ultimate_bitnet_final.pth")
        logger.info("✓ تم الحفظ بصيغة .pth بدلاً من safetensors")
    
    logger.info("\n🎉 انتهى التشغيل بنجاح!")
    logger.info(f"📁 الملفات المحفوظة في: {config.checkpoint_dir}")
