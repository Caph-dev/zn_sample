"""Versioned, I/O-free message templates copied from authoritative SOP2."""
from __future__ import annotations

import unicodedata

from assistant.domain.policies import followup_message_creator_type


TEMPLATE_VERSION = 1

_TEMPLATES = {
    "arrival_hero_video_en": """Hi {creator_name}! ❤️

I just wanted to check in and see if you’ve received the product yet. 😊

We recently started running automatic ad boosting for content featuring this product. If you could add **#Hicloth** when posting your video, it will help our system recognize your content more accurately and give your video more opportunities to reach the right audience. ✨

We’ve also prepared a product selling points image for you. Feel free to use it as a reference when creating your content and highlight some of the key features in your video, which can help your audience better understand the product and its benefits. 💕

If you have any questions or need any support, please feel free to reach out to me anytime. I’m always happy to help! 🥰""",
    "arrival_hero_video_es": """Hola{creator_name} ! ❤️

Solo quería pasar por aquí para confirmar si ya recibiste el producto. 😊

Recientemente hemos comenzado a utilizar publicidad automática para impulsar los videos relacionados con este producto. Si puedes añadir **#Hicloth** al publicar tu video, ayudará a que nuestro sistema identifique tu contenido con mayor precisión y le dará más oportunidades de llegar a la audiencia adecuada. ✨

También hemos preparado una imagen con los puntos de venta principales del producto para ti. Puedes utilizarla como referencia al crear tu contenido y mencionar algunas de estas características en tu video, lo que ayudará a que tu audiencia conozca mejor el producto y sus beneficios. 💕

Si tienes cualquier pregunta o necesitas ayuda, no dudes en contactarme en cualquier momento. ¡Estaré encantada de ayudarte! 🥰""",
    "arrival_hero_live_en": """Hi {creator_name}❤️

Just checking in to see if you’ve received the product yet! 😊 We really love your live streams and would love to build a long-term partnership with you. ✨

For great-fit creators, we can offer exclusive live flash sales and extra support to help boost traffic and sales. 🚀

We’ve also prepared a product selling-points image for your reference during your live. If you need any help, feel free to reach out anytime! 🥰""",
    "arrival_hero_live_es": """¡Hola {creator_name}! ❤️

Solo quería saber si ya recibiste el producto. 😊 Nos encantan tus transmisiones en vivo y nos gustaría mucho trabajar contigo a largo plazo. ✨

Para los creadores que encajan bien con nuestra marca, podemos ofrecer promociones exclusivas durante los lives y apoyo adicional para ayudarte a aumentar el tráfico y las ventas. 🚀

También preparamos una imagen con los puntos clave del producto para que puedas usarla como referencia durante tu live. Si necesitas cualquier ayuda, ¡no dudes en escribirme! 🥰""",
    "arrival_other_video_en": """Hi {creator_name}! ❤️

I just wanted to check in and see if you’ve received the product yet. 😊 We’re so excited to see your content and can’t wait to see how you style and share it with your audience!

We recently started running automatic ad boosting for videos featuring this product, so if you could add **#Hicloth** when posting, it can help your content get discovered by more of the right audience and bring more exposure to your video. ✨

If you have any questions about the product, content ideas, or need any support from our side, please feel free to reach out anytime. We’re always happy to help! 💕""",
    "arrival_other_video_es": """Hola{creator_name}! ❤️

Solo quería pasar por aquí para confirmar si ya recibiste el producto. 😊 ¡Estamos muy emocionados de ver tu contenido y nos encantaría ver cómo lo muestras y compartes con tu audiencia!

Recientemente hemos comenzado a impulsar automáticamente los videos relacionados con este producto mediante publicidad, así que si puedes añadir el hashtag **#Hicloth** al publicar tu video, ayudará a que tu contenido pueda llegar a una audiencia más adecuada y obtener más exposición. ✨

Si tienes cualquier pregunta sobre el producto, necesitas ideas para tu contenido o cualquier tipo de ayuda por nuestra parte, no dudes en escribirme cuando quieras. ¡Estaremos encantados de apoyarte! 💕""",
    "arrival_other_live_en": """Hi {creator_name}! ❤️

I just wanted to check in and see if you’ve received the product safely. 😊

We’re excited to have the opportunity to work with you! For creators who enjoy going live, we can provide exclusive live flash sale events and additional support to help you create a better shopping experience for your audience and maximize your results. ✨

If you have any upcoming live plans or there’s anything you need from our side (product details, content ideas, promotions, etc.), please don’t hesitate to reach out anytime. I’ll do my best to support and coordinate everything for you! 💕

Can’t wait to see your content and hopefully work together more! 🥰""",
    "arrival_other_live_es": """Hola{creator_name}! ❤️

Solo quería pasar por aquí para confirmar si ya recibiste el producto correctamente. 😊

¡Estamos muy emocionados de tener la oportunidad de colaborar contigo! Para las creadoras que disfrutan hacer transmisiones en vivo, podemos ofrecer campañas exclusivas de descuentos en tus lives y apoyo adicional para ayudarte a crear una mejor experiencia de compra para tu audiencia y obtener mejores resultados. ✨

Si tienes algún plan de hacer un live próximamente o necesitas cualquier ayuda de nuestra parte (información del producto, ideas de contenido, promociones, etc.), no dudes en escribirme cuando quieras. Haré todo lo posible para apoyarte y coordinar todo contigo. 💕

¡Tenemos muchas ganas de ver tu contenido y seguir colaborando contigo! 🥰""",
    "unpublished_3_video_en": """Hi{creator_name} ! ❤️

Just a friendly reminder that our product is currently running a limited-time promotion. ✨

We’re excited to see your video after trying the product! If you have any questions or need any help while creating your content, feel free to reach out anytime. 😊""",
    "unpublished_3_video_es": """Hola{creator_name}! ❤️

Solo quería recordarte que el producto con el que estamos colaborando tiene una promoción especial por tiempo limitado. ✨

¡Tenemos muchas ganas de ver tu video después de probar el producto! Si tienes cualquier pregunta o necesitas ayuda durante la creación de tu contenido, no dudes en escribirme. 😊""",
    "unpublished_3_live_en": """Hi {creator_name}! ❤️

Just a friendly reminder that the product we’re collaborating on is currently running a limited-time promotion. ✨

We’re really looking forward to seeing you showcase and test the product during your next live stream! If you need any help during your live session, please feel free to contact me anytime. 😊

We’d be happy to support you and look forward to having a great collaboration together! 💕""",
    "unpublished_3_live_es": """Hola{creator_name}! ❤️

Solo quería recordarte que el producto con el que estamos colaborando actualmente tiene una promoción especial por tiempo limitado. ✨

¡Tenemos muchas ganas de verte probar el producto en tu próximo live! Si necesitas cualquier ayuda durante tu transmisión, no dudes en contactarme en cualquier momento. 😊

¡Estaremos encantados de apoyarte y esperamos tener una excelente colaboración contigo! 💕""",
    "unpublished_7_video_en": """Hi {creator_name}! ❤️

We’re currently running a creator video promotion campaign this week, and we’d love to include your content in it! ✨

If you’re able to share your video soon, we can add it to our advertising campaign to help increase its exposure and bring more traffic to your content. 🚀

We’re excited to see your creation and can’t wait to support your video! 😊""",
    "unpublished_7_video_es": """Hola {creator_name}! ❤️

Esta semana estamos realizando una campaña de promoción de videos de creadores y nos encantaría incluir tu contenido en ella. ✨

Si puedes publicar tu video pronto, podremos añadirlo a nuestra campaña de publicidad para ayudarte a aumentar la exposición de tu contenido y atraer más tráfico a tu video. 🚀

¡Tenemos muchas ganas de ver tu creación y apoyarte para que tu video tenga mejores resultados! 😊""",
    "unpublished_7_live_en": """Hi {creator_name}! ❤️

I wanted to check if you have any upcoming live plans. 😊

We’ve noticed that this product has been getting great exposure and attention recently, so we were thinking this could be a great time to go live and take advantage of the current traffic. ✨

With the product’s strong momentum and your amazing ability to connect with and recommend products to your audience, we believe it could bring great results together. 🚀

If you’d like, we can also set up an exclusive live flash sale event for you to help boost your sales. And if there’s anything you need support with before or during your live stream, please feel free to let me know anytime. I’ll do my best to help! 💕""",
    "unpublished_7_live_es": """Hola{creator_name}! ❤️

Quería preguntarte si tienes algún plan de hacer un live próximamente. 😊

Hemos notado que este producto ha estado recibiendo muy buena exposición y atención últimamente, así que creemos que podría ser un buen momento para aprovechar el tráfico actual y hacer una transmisión en vivo. ✨

Con el buen rendimiento del producto y tu gran capacidad para conectar con tu audiencia y recomendar productos, creemos que juntos podríamos conseguir muy buenos resultados. 🚀

Si te interesa, también podemos ayudarte a configurar una campaña exclusiva de descuento para tu live y así impulsar tus ventas. Además, si necesitas cualquier apoyo antes o durante tu transmisión, no dudes en escribirme en cualquier momento. ¡Haré todo lo posible por ayudarte! 💕""",
    "video_found_en": """Hi {creator_name}! ❤️

We just saw the video you created for us and really appreciate your support and effort in showcasing our brand. Thank you so much! 🥰We love your content style and creativity. Would you be interested in creating 1–2 more videos for this product? ✨

We’d also love to add your videos to our advertising campaign and provide boosting support to help more people discover your content. 🚀We truly appreciate your creativity and would love to explore a long-term partnership with you! 💕

{content_url}""",
    "video_found_es": """Hola{creator_name}! ❤️

Acabamos de ver el video que creaste para nosotros y realmente apreciamos mucho tu apoyo y el esfuerzo que pusiste en mostrar nuestra marca. ¡Muchas gracias! 🥰Nos encanta tu estilo de contenido y tu creatividad. ¿Te interesaría crear 1–2 videos más para este producto? ✨

También nos gustaría incluir tus videos en nuestra campaña de publicidad y brindar apoyo de promoción para ayudar a que más personas descubran tu contenido. 🚀Valoramos mucho tu creatividad y nos encantaría explorar una colaboración a largo plazo contigo. 💕

{content_url}""",
    "live_found_en": """Hi {creator_name}! ❤️

We noticed your recent live stream and wanted to sincerely thank you for all the effort and support you’ve put into promoting our brand. We truly appreciate it! 🥰

We also saw that your live performance had great results, so we specially requested an exclusive live flash sale event for you. We hope this can help attract more customers, boost your sales, and bring you even more orders. 🚀

Would you be interested in featuring this product again in your upcoming live streams? We’d love to continue supporting you with more resources and work together to achieve even better results! ✨""",
    "live_found_es": """Hola {creator_name}! ❤️

Hemos visto tu reciente transmisión en vivo y queremos agradecerte sinceramente por todo el esfuerzo y apoyo que has dedicado a promocionar nuestra marca. ¡Lo apreciamos muchísimo! 🥰

También notamos que tu live tuvo muy buenos resultados, por eso solicitamos especialmente una campaña exclusiva de descuento para tu transmisión. Esperamos que esto pueda ayudarte a atraer más clientes, aumentar tus ventas y generar aún más pedidos. 🚀

¿Te gustaría seguir mostrando y promocionando este producto en tus próximos lives? Nos encantaría seguir apoyándote con más recursos y trabajar juntas para conseguir resultados aún mejores. ✨""",
}


def choose_template_key(
    *,
    stage: str,
    creator_type: str | None,
    lang: str,
    is_hero_sku: bool,
) -> str | None:
    """Choose a SOP2 template key for the current follow-up context."""
    if lang not in {"en", "es"}:
        return None
    if stage in {"day_10_list", "unfulfilled", "confirm_delivery_time"}:
        return None
    if stage == "video_found":
        return f"video_found_{lang}"
    if stage == "live_found":
        return f"live_found_{lang}"
    message_creator_type = followup_message_creator_type(creator_type)
    if stage == "content_found" and message_creator_type == "video":
        return f"video_found_{lang}"
    if stage == "content_found" and message_creator_type == "live":
        return f"live_found_{lang}"
    if message_creator_type not in {"video", "live"}:
        return None
    if stage == "arrival":
        sku_group = "hero" if is_hero_sku else "other"
        return f"arrival_{sku_group}_{message_creator_type}_{lang}"
    if stage == "day_3":
        return f"unpublished_3_{message_creator_type}_{lang}"
    if stage == "day_7":
        return f"unpublished_7_{message_creator_type}_{lang}"
    return None


CONTENT_THANKS_FINGERPRINTS = (
    "we just saw the video you created for us",
    "acabamos de ver el video que creaste para nosotros",
    "we noticed your recent live stream",
    "hemos visto tu reciente transmision en vivo",
)

_UNCERTAIN_THANKS_PAIRS = (
    ("thank you", "video"),
    ("thank you", "live"),
    ("gracias", "video"),
    ("gracias", "live"),
    ("appreciate your support", ""),
    ("appreciate your creativity", ""),
    ("apreciamos muchisimo", ""),
)

# 同事风格的感谢要落在同一段话里才算数；否则旧消息里的 "thank you" 会与本轮
# 无关的 "video" 拼成假阳性，把正常提醒挡成待人工确认。
UNCERTAIN_THANKS_PROXIMITY_CHARS = 120


def _fold_thanks_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def looks_like_content_thanks(text: str) -> bool:
    """True only when the thread already contains SOP content-thanks copy."""
    blob = _fold_thanks_text(text)
    return any(token in blob for token in CONTENT_THANKS_FINGERPRINTS)


def looks_like_uncertain_thanks(text: str) -> bool:
    """Colleague-style thanks that is not the SOP fingerprint.

    The thanks and the product/content word must appear close together: an old
    "thank you for collaborating" plus a "video" mentioned in a different
    message must not park an unrelated reminder for human review.
    """
    if looks_like_content_thanks(text):
        return False
    blob = _fold_thanks_text(text)
    for left, right in _UNCERTAIN_THANKS_PAIRS:
        left_index = blob.find(left)
        while left_index != -1:
            if not right:
                return True
            window_start = max(0, left_index - UNCERTAIN_THANKS_PROXIMITY_CHARS)
            window_end = left_index + len(left) + UNCERTAIN_THANKS_PROXIMITY_CHARS
            if right in blob[window_start:window_end]:
                return True
            left_index = blob.find(left, left_index + 1)
    return False


def looks_like_any_thanks(text: str) -> bool:
    """SOP thanks copy or a colleague-style thanks message."""
    return looks_like_content_thanks(text) or looks_like_uncertain_thanks(text)


def render_followup_message(
    template_key: str,
    *,
    creator_name: str,
    content_url: str = "",
) -> str:
    """Render one known template, rejecting unknown keys explicitly."""
    try:
        template = _TEMPLATES[template_key]
    except KeyError as error:
        raise ValueError(f"Unknown follow-up template: {template_key}") from error
    return template.format(creator_name=creator_name, content_url=content_url)
