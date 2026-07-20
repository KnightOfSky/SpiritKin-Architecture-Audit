import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.generation import GenerationConfig


class QwenVLAnalyzer:
    def __init__(self, model_name="Qwen/Qwen-VL-Chat", device="cuda"):
        self.device = device if torch.cuda.is_available() else "cpu"
        print(f"🧠 正在加载 Qwen-VL 模型到 {self.device}...")

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name, trust_remote_code=True
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            device_map="auto",
            trust_remote_code=True,
            bf16=True if self.device == "cuda" else False,
            load_in_4bit=self.device == "cuda"  # 自动 4bit 量化（节省显存）
        ).eval()

        # 启用 Flash Attention（若安装）
        try:
            self.model.transformer.enable_flash_attn()
        except Exception:
            pass

        print("✅ Qwen-VL 加载完成！")

    def analyze_image(self, image_path: str, query: str) -> str:
        """
        使用 Qwen-VL 分析图像并回答问题
        :param image_path: 图像路径
        :param query: 自然语言问题（如“图中有什么按钮？”）
        :return: 模型回答
        """
        try:
            query_with_img = self.tokenizer.from_list_format([
                {'image': image_path},
                {'text': query},
            ])
            response, _ = self.model.chat(
                self.tokenizer,
                query=query_with_img,
                history=[],
                generation_config=GenerationConfig(max_new_tokens=512, top_p=0.8)
            )
            return response.strip()
        except Exception as e:
            print(f"❌ Qwen-VL 推理失败: {e}")
            return ""