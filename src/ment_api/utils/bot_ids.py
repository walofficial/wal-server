from ment_api.persistence.models.external_article import NewsSource


def bot_name_to_id():
    return {
        NewsSource.IMEDI.value: "85979cb6-edb4-4e91-93bb-a4c03b4d5893",
        NewsSource.PUBLIKA.value: "fb878ccb-c94d-40a3-801f-b0d054d8f880",
        NewsSource.TV1.value: "483c14a4-d93f-4810-9e30-128394969e4e",
        NewsSource.INTERPRESS.value: "743939e1-5f8b-4b8c-ad20-f42e589c2bd9",
    }
