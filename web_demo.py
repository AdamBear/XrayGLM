#!/usr/bin/env python
import os
os.environ['SAT_HOME'] = '/data/XrayGLM'

import gradio as gr
from PIL import Image
import os
import json
from model import is_chinese, generate_input, chat
import torch
import argparse
from transformers import AutoTokenizer
from model import VisualGLMModel, chat
from finetune_XrayGLM import FineTuneVisualGLMModel
from sat.model import AutoModel
from sat.model.mixins import CachedAutoregressiveMixin
from sat.quantization.kernels import quantize

def generate_text_with_image(input_text, image, history=[], request_data=dict(), is_zh=True):
    input_para = {
        "max_length": 2048,
        "min_length": 50,
        "temperature": 0.8,
        "top_p": 0.4,
        "top_k": 100,
        "repetition_penalty": 1.2
    }
    input_para.update(request_data)

    input_data = generate_input(input_text, image, history, input_para, image_is_encoded=False)
    input_image, gen_kwargs =  input_data['input_image'], input_data['gen_kwargs']
    with torch.no_grad():
        answer, history, _ = chat(None, model, tokenizer, input_text, history=history, image=input_image, \
                            max_length=gen_kwargs['max_length'], top_p=gen_kwargs['top_p'], \
                            top_k = gen_kwargs['top_k'], temperature=gen_kwargs['temperature'], english=not is_zh)
    return answer


def request_model(input_text, temperature, top_p, image_prompt, result_previous):
    result_text = [(ele[0], ele[1]) for ele in result_previous]
    for i in range(len(result_text)-1, -1, -1):
        if result_text[i][0] == "" or result_text[i][1] == "":
            del result_text[i]
    print(f"history {result_text}")

    is_zh = is_chinese(input_text)
    if image_prompt is None:
        if is_zh:
            result_text.append((input_text, '图片为空！请上传图片并重试。'))
        else:
            result_text.append((input_text, 'Image empty! Please upload a image and retry.'))
        return input_text, result_text
    elif input_text == "":
        result_text.append((input_text, 'Text empty! Please enter text and retry.'))
        return "", result_text                

    request_para = {"temperature": temperature, "top_p": top_p}
    image = Image.open(image_prompt)
    try:
        answer = generate_text_with_image(input_text, image, result_text.copy(), request_para, is_zh)
    except Exception as e:
        print(f"error: {e}")
        if is_zh:
            result_text.append((input_text, '超时！请稍等几分钟再重试。'))
        else:
            result_text.append((input_text, 'Timeout! Please wait a few minutes and retry.'))
        return "", result_text

    result_text.append((input_text, answer))
    print(result_text)
    return "", result_text


DESCRIPTION = '''# <a href=".">企友通看胸片医学助手演示</a>'''

NOTES = 'This app is adapted from <a href="https://github.com/WangRongsheng/XrayGLM">https://github.com/WangRongsheng/XrayGLM</a>. It would be recommended to check out the repo if you want to see the detail of our model and training process.'


def clear_fn(value):
    return "请描述这张胸片", [("", "")], None

def clear_fn2(value):
    return [("", "", "")]


def main(args):
    global model, tokenizer


    with gr.Blocks(css='style.css') as demo:
        gr.Markdown(DESCRIPTION)
        with gr.Row():
            with gr.Column(scale=4.5):
                with gr.Group():
                    image_prompt = gr.Image(type="filepath", label="上传胸片", value=None)
                    input_text = gr.Textbox(label='输入框', placeholder='请输入问题', value="请描述这张胸片")
                    with gr.Row():
                        run_button = gr.Button('发送')
                        clear_button = gr.Button('清除')

                    gr.Examples(label="示例胸片", examples=[
                        "./xray_images/5_1.png",
                        "./xray_images/218_1.png",
                        "./xray_images/2201_1.png",
                        "./xray_images/1329_1.png",
                        "./xray_images/1668_1.png",
                        "./xray_images/22_1.png",
                        "./xray_images/38_2.png",
                    ],
                                inputs=image_prompt, outputs=[input_text, None, None], fn=clear_fn, cache_examples=False)
                with gr.Row():
                    temperature = gr.Slider(maximum=1, value=0.8, minimum=0, label='Temperature', visible=False)
                    top_p = gr.Slider(maximum=1, value=0.4, minimum=0, label='Top P', visible=False)


            with gr.Column(scale=5.5):
                result_text = gr.components.Chatbot(label='对话历史', value=[("", "请描述这张胸片")]).style(height=750)

        #gr.Markdown(NOTES)

        print(gr.__version__)
        run_button.click(fn=request_model,inputs=[input_text, temperature, top_p, image_prompt, result_text],
                         outputs=[input_text, result_text])
        input_text.submit(fn=request_model, inputs=[input_text, temperature, top_p, image_prompt, result_text],
                         outputs=[input_text, result_text])
        clear_button.click(fn=clear_fn, inputs=clear_button, outputs=[input_text, result_text, image_prompt])
        image_prompt.upload(fn=clear_fn2, inputs=clear_button, outputs=[input_text, result_text])
        image_prompt.clear(fn=clear_fn2, inputs=clear_button, outputs=[input_text, result_text])




    # load model
    model, model_args = AutoModel.from_pretrained(
        args.from_pretrained,
        args=argparse.Namespace(
            fp16=True,
            skip_init=True,
            use_gpu_initialization=True if (torch.cuda.is_available() and args.quant is None) else False,
            device='cuda' if (torch.cuda.is_available() and args.quant is None) else 'cpu',
        ))
    model = model.eval()

    if args.quant:
        quantize(model.transformer, args.quant)

    if torch.cuda.is_available():
        model = model.cuda()

    model.add_mixin('auto-regressive', CachedAutoregressiveMixin())

    tokenizer = AutoTokenizer.from_pretrained("/data/chatglm-6b", trust_remote_code=True)

    print(gr.__version__)

    demo.queue(concurrency_count=10).launch(share=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--quant", choices=[8, 4], type=int, default=None)
    parser.add_argument("--share", action="store_true")
    parser.add_argument("--from_pretrained", type=str, default="checkpoints", help='pretrained ckpt')
    args = parser.parse_args()
    main(args)
