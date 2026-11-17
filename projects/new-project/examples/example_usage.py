"""
مثال عملي لاستخدام Ultimate BitNet المحسّن
====================================================

هذا الملف يوضح كيفية استخدام النموذج مع بيانات حقيقية
"""

import torch
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from PIL import Image
import json
from pathlib import Path
from typing import List, Tuple, Dict
import logging

from ultimate_bitnet_improved import (
    UltimateBitNet, 
    ModelConfig,
    train_model,
    get_transforms,
    save_checkpoint,
    load_checkpoint
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 1. إنشاء Dataset مخصص لبياناتك
# ═══════════════════════════════════════════════════════════════════════════════

class CustomMultiModalDataset(Dataset):
    """
    Dataset مخصص للبيانات متعددة الوسائط (نص + صورة)
    
    توقع البيانات بالشكل التالي:
    data/
    ├── images/
    │   ├── img_001.jpg
    │   ├── img_002.jpg
    │   └── ...
    └── annotations.json  # {"img_001.jpg": {"text": "...", "label": 0}}
    """
    
    def __init__(self, 
                 data_dir: str,
                 annotations_file: str,
                 vocab: Dict[str, int],
                 max_seq_length: int = 128,
                 transform=None,
                 is_training: bool = True):
        """
        Args:
            data_dir: مسار مجلد البيانات
            annotations_file: ملف JSON يحتوي على النصوص والـ labels
            vocab: قاموس الكلمات {word: index}
            max_seq_length: أقصى طول للنص
            transform: تحويلات الصورة
            is_training: هل هذا dataset للتدريب؟
        """
        self.data_dir = Path(data_dir)
        self.max_seq_length = max_seq_length
        self.vocab = vocab
        self.transform = transform or get_transforms(is_training)
        
        # تحميل الـ annotations
        with open(annotations_file, 'r', encoding='utf-8') as f:
            self.annotations = json.load(f)
        
        self.image_ids = list(self.annotations.keys())
        logger.info(f"تم تحميل {len(self.image_ids)} عينة من {annotations_file}")
    
    def __len__(self):
        return len(self.image_ids)
    
    def text_to_indices(self, text: str) -> torch.Tensor:
        """تحويل النص إلى indices"""
        # تقسيم النص إلى كلمات (tokenization بسيط)
        words = text.lower().split()
        
        # تحويل الكلمات إلى indices
        indices = []
        for word in words[:self.max_seq_length]:
            # استخدام index الكلمة أو <UNK> token
            idx = self.vocab.get(word, self.vocab.get('<UNK>', 1))
            indices.append(idx)
        
        # Padding
        while len(indices) < self.max_seq_length:
            indices.append(0)  # <PAD> = 0
        
        return torch.tensor(indices, dtype=torch.long)
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, int]:
        """
        Returns:
            text: (max_seq_length,) - token indices
            image: (3, 224, 224) - preprocessed image
            label: int - class label
        """
        img_id = self.image_ids[idx]
        annotation = self.annotations[img_id]
        
        # تحميل الصورة
        img_path = self.data_dir / 'images' / img_id
        try:
            image = Image.open(img_path).convert('RGB')
            if self.transform:
                image = self.transform(image)
        except Exception as e:
            logger.error(f"خطأ في تحميل الصورة {img_path}: {e}")
            # صورة افتراضية في حالة الخطأ
            image = torch.zeros(3, 224, 224)
        
        # معالجة النص
        text = annotation['text']
        text_tensor = self.text_to_indices(text)
        
        # الـ label
        label = annotation['label']
        
        return text_tensor, image, label

# ═══════════════════════════════════════════════════════════════════════════════
# 2. بناء القاموس (Vocabulary)
# ═══════════════════════════════════════════════════════════════════════════════

def build_vocab_from_annotations(annotations_file: str, 
                                 min_freq: int = 2,
                                 max_vocab_size: int = 10000) -> Dict[str, int]:
    """
    بناء قاموس الكلمات من ملف الـ annotations
    
    Args:
        annotations_file: ملف JSON
        min_freq: الحد الأدنى لتكرار الكلمة
        max_vocab_size: أقصى حجم للقاموس
    
    Returns:
        vocab: {word: index}
    """
    from collections import Counter
    
    # قراءة جميع النصوص
    with open(annotations_file, 'r', encoding='utf-8') as f:
        annotations = json.load(f)
    
    # عد تكرار الكلمات
    word_counts = Counter()
    for img_id, data in annotations.items():
        words = data['text'].lower().split()
        word_counts.update(words)
    
    # اختيار الكلمات الأكثر تكراراً
    vocab = {'<PAD>': 0, '<UNK>': 1}
    
    for word, count in word_counts.most_common(max_vocab_size - 2):
        if count >= min_freq:
            vocab[word] = len(vocab)
    
    logger.info(f"تم بناء قاموس بحجم {len(vocab)} كلمة")
    return vocab

# ═══════════════════════════════════════════════════════════════════════════════
# 3. مثال تدريب كامل
# ═══════════════════════════════════════════════════════════════════════════════

def example_training():
    """مثال كامل لتدريب النموذج على بيانات حقيقية"""
    
    # 1. الإعدادات
    config = ModelConfig()
    config.num_epochs = 30
    config.batch_size = 32
    config.lr = 1e-3
    config.use_wandb = False  # غيّرها إلى True لاستخدام W&B
    
    # 2. بناء القاموس
    vocab = build_vocab_from_annotations(
        'data/train_annotations.json',
        min_freq=2,
        max_vocab_size=config.vocab_size
    )
    
    # حفظ القاموس للاستخدام لاحقاً
    with open('vocab.json', 'w', encoding='utf-8') as f:
        json.dump(vocab, f, ensure_ascii=False, indent=2)
    
    config.vocab_size = len(vocab)
    
    # 3. إنشاء Datasets
    train_dataset = CustomMultiModalDataset(
        data_dir='data',
        annotations_file='data/train_annotations.json',
        vocab=vocab,
        max_seq_length=config.max_seq_length,
        transform=get_transforms(is_training=True),
        is_training=True
    )
    
    val_dataset = CustomMultiModalDataset(
        data_dir='data',
        annotations_file='data/val_annotations.json',
        vocab=vocab,
        max_seq_length=config.max_seq_length,
        transform=get_transforms(is_training=False),
        is_training=False
    )
    
    # 4. DataLoaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=config.num_workers,
        pin_memory=True
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.num_workers,
        pin_memory=True
    )
    
    # 5. إنشاء النموذج
    model = UltimateBitNet(config).to(config.device)
    
    # 6. التدريب
    logger.info("بدء التدريب...")
    trained_model = train_model(
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        cfg=config
    )
    
    logger.info("✅ انتهى التدريب بنجاح!")
    
    return trained_model, config, vocab

# ═══════════════════════════════════════════════════════════════════════════════
# 4. الاستدلال (Inference)
# ═══════════════════════════════════════════════════════════════════════════════

def predict_single_sample(model: UltimateBitNet,
                         image_path: str,
                         text: str,
                         vocab: Dict[str, int],
                         config: ModelConfig,
                         class_names: List[str] = None) -> Dict:
    """
    التنبؤ على عينة واحدة
    
    Args:
        model: النموذج المدرب
        image_path: مسار الصورة
        text: النص المرافق
        vocab: قاموس الكلمات
        config: إعدادات النموذج
        class_names: أسماء الفئات (اختياري)
    
    Returns:
        {
            'predicted_class': int,
            'predicted_label': str,
            'probabilities': [float, ...],
            'confidence': float
        }
    """
    model.eval()
    
    # معالجة الصورة
    transform = get_transforms(is_training=False)
    image = Image.open(image_path).convert('RGB')
    image = transform(image).unsqueeze(0).to(config.device)
    
    # معالجة النص
    words = text.lower().split()
    indices = []
    for word in words[:config.max_seq_length]:
        idx = vocab.get(word, vocab.get('<UNK>', 1))
        indices.append(idx)
    while len(indices) < config.max_seq_length:
        indices.append(0)
    
    text_tensor = torch.tensor([indices], dtype=torch.long).to(config.device)
    text_lengths = torch.tensor([len([i for i in indices if i != 0])])
    
    # التنبؤ
    with torch.no_grad():
        logits = model(text_tensor, image, text_lengths)
        probabilities = torch.softmax(logits, dim=1)
        predicted_class = logits.argmax(dim=1).item()
        confidence = probabilities[0, predicted_class].item()
    
    result = {
        'predicted_class': predicted_class,
        'predicted_label': class_names[predicted_class] if class_names else str(predicted_class),
        'probabilities': probabilities[0].cpu().tolist(),
        'confidence': confidence
    }
    
    return result

# ═══════════════════════════════════════════════════════════════════════════════
# 5. الاستخدام السريع
# ═══════════════════════════════════════════════════════════════════════════════

def quick_start_example():
    """مثال للبدء السريع"""
    
    print("="*60)
    print("مثال الاستخدام السريع لـ Ultimate BitNet")
    print("="*60)
    
    # تحميل القاموس (إذا كان موجوداً)
    try:
        with open('vocab.json', 'r', encoding='utf-8') as f:
            vocab = json.load(f)
        print(f"✓ تم تحميل القاموس ({len(vocab)} كلمة)")
    except:
        print("⚠ لم يتم العثور على vocab.json، استخدام قاموس افتراضي")
        vocab = {'<PAD>': 0, '<UNK>': 1, 'test': 2, 'sample': 3}
    
    # إعدادات النموذج
    config = ModelConfig()
    config.vocab_size = len(vocab)
    config.num_classes = 3  # غيّرها حسب عدد الفئات لديك
    
    # إنشاء النموذج
    model = UltimateBitNet(config).to(config.device)
    print(f"✓ تم إنشاء النموذج على {config.device}")
    
    # محاولة تحميل أوزان مدربة
    try:
        epoch = load_checkpoint(
            model=model,
            checkpoint_path="checkpoints/best_model.safetensors",
            cfg=config
        )
        print(f"✓ تم تحميل نموذج من Epoch {epoch}")
    except:
        print("⚠ لم يتم العثور على نموذج مدرب، استخدام أوزان عشوائية")
    
    # مثال على التنبؤ
    print("\n" + "-"*60)
    print("مثال على التنبؤ:")
    print("-"*60)
    
    # أسماء الفئات (غيّرها حسب مشكلتك)
    class_names = ['سلبي', 'محايد', 'إيجابي']
    
    try:
        result = predict_single_sample(
            model=model,
            image_path='sample_image.jpg',  # استبدل بصورة حقيقية
            text='هذا نص تجريبي للاختبار',
            vocab=vocab,
            config=config,
            class_names=class_names
        )
        
        print(f"التصنيف المتوقع: {result['predicted_label']}")
        print(f"الثقة: {result['confidence']:.2%}")
        print(f"الاحتمالات:")
        for i, prob in enumerate(result['probabilities']):
            print(f"  {class_names[i]}: {prob:.2%}")
    except Exception as e:
        print(f"❌ خطأ في التنبؤ: {e}")
        print("   تأكد من وجود ملف sample_image.jpg")

# ═══════════════════════════════════════════════════════════════════════════════
# 6. Main
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Ultimate BitNet - مثال الاستخدام')
    parser.add_argument('--mode', type=str, default='demo', 
                       choices=['demo', 'train', 'predict'],
                       help='وضع التشغيل')
    parser.add_argument('--image', type=str, help='مسار الصورة للتنبؤ')
    parser.add_argument('--text', type=str, help='النص للتنبؤ')
    
    args = parser.parse_args()
    
    if args.mode == 'demo':
        print("\n🚀 تشغيل المثال التوضيحي...\n")
        quick_start_example()
    
    elif args.mode == 'train':
        print("\n🏋️ بدء التدريب...\n")
        print("⚠ تأكد من وجود:")
        print("   - data/train_annotations.json")
        print("   - data/val_annotations.json")
        print("   - data/images/\n")
        
        response = input("هل تريد المتابعة؟ (y/n): ")
        if response.lower() == 'y':
            try:
                model, config, vocab = example_training()
                print("\n✅ اكتمل التدريب!")
            except Exception as e:
                print(f"\n❌ خطأ في التدريب: {e}")
        else:
            print("تم الإلغاء.")
    
    elif args.mode == 'predict':
        if not args.image or not args.text:
            print("❌ يجب توفير --image و --text")
        else:
            print(f"\n🔮 التنبؤ على:")
            print(f"   الصورة: {args.image}")
            print(f"   النص: {args.text}\n")
            
            quick_start_example()  # سيتم استخدام المعاملات المحددة
    
    print("\n" + "="*60)
    print("للمزيد من المعلومات، راجع README_AR.md")
    print("="*60)
