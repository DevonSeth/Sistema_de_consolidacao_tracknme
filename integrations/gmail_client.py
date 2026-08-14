"""
Cliente de envio de e-mail via Gmail API, usando OAuth2 (conta pessoal
operacionaljessyca@gmail.com — sem domain-wide delegation, por não ser
Workspace).

Pré-requisito (pendência registrada, fazer antes de implementar isto):
    Gerar o refresh_token uma única vez, via fluxo de autorização OAuth
    (o usuário loga uma vez no navegador, autoriza o app, o token gerado
    entra na config/seed).

TODO:
    enviar_email(destinatarios: list[str], assunto: str, corpo: str,
                 anexos: list[str] | None = None) -> None
        Usa o refresh_token da config para obter um access_token temporário
        a cada envio (não expira por uso).
"""


def enviar_email(destinatarios: list[str], assunto: str, corpo: str,
                  anexos: list[str] | None = None) -> None:
    raise NotImplementedError
