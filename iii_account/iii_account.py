import logging
import requests
import time
from pyquery import PyQuery as pq

OPAC_BASE_URL = 'https://josiah.brown.edu/'

log = logging.getLogger(__name__)

class IIIAccount():

    def __init__(self, name, barcode):
        self.name = name
        self.barcode = barcode
        self.patron_id = None
        self.session_id = None
        self.cookies = None
        self.opac_url = OPAC_BASE_URL
        self.request_base = self.opac_url + 'search~S7?/.{{bib}}/.{{bib}}/1%2C1%2C1%2CB/request~{{bib}}'
        self.session = requests.Session()
        self.session.verify = False

    def login(self):
        payload = {
            'name' : self.name,
            'code' : self.barcode,
            'pat_submit':'xxx'
        }
        """
        extpatid:
        extpatpw:
        name:
        code:
        pat_submit:xxx
        """
        out = {
               'username': self.name,
               'authenticated': False,
               'url': None}
        url = self.opac_url + 'patroninfo'
        rsp = self.session.post(url, data=payload, allow_redirects=True)
        content = rsp.content
        doc = pq(rsp.content)
        # Span.login_error will also appear in content if login fails.
        login_error = doc('span.login_error').val()
        if (login_error):
            raise Exception("Login failed.")
        else:
            out['authenticated'] = True
            url = rsp.url
            out['url'] = url
            out['patron_id'] = url.split('/')[-2]
            self.patron_id = out['patron_id']
            log.info("Patron {} authenticated.".format({self.patron_id}))
            return out

    def logout(self):
        """
        This seems to be pretty straight forward.  Just hit the url and session
        cookies.  Then carry on.
        """
        url = self.opac_url + 'logout~S7?'
        rsp = self.session.get(url)
        #Add check to verify text or url is what is expected.
        return True

    def _validate_session(self, content):
        if 'your validation has expired' in content.lower():
            raise Exception("Validation expired.")


    def get_holds(self):
        """
        Return a list of holds for a user.
        """
        url = self.opac_url + 'patroninfo~S7/%s/holds' % self.patron_id
        rsp = self.session.get(url)
        #error checking to see if we logged in?
        content = rsp.content
        self._validate_session(content)
        holds = self._parse_holds_list(content)
        return holds

    def _parse_holds_list(self, content):
        """
        Private method for parsing hold response.
        """
        doc = pq(content)
        hold_rows = doc('.patFuncEntry')
        def _get(chunk, selector):
            """
            little util to get text by css selector.
            """
            return chunk.cssselect('td.%s' % selector)[0].text_content().strip()
        holds = [
            {
                 'key': row.cssselect('input')[0].attrib['id'],
                 'title': _get(row, 'patFuncTitle'),
                 'status': _get(row, 'patFuncStatus'),
                 'pickup': _get(row, 'patFuncPickup'),
                 'cancel_by': _get(row, 'patFuncCancel')
            }
            for row in hold_rows
        ]
        return holds

    def get_items(self, bib):
        """
        Get the item numbers linked to a bib record.  If no item number is
        returned, this item isn't requestable.
        """
        url = self.request_base.replace('{{bib}}', bib)
        payload = {
            'name' : self.name,
            'code' : self.barcode,
            'pat_submit':'xxx',
            'neededby_Month': '2',
            'neededby_Day': '1',
            'neededby_Year': '2011',
            'submit': 'SUBMIT',
            'loc': 'ROCK',
            #inum is optional
        }
        r = requests.post(url,
                          data=payload,
                          cookies=self.cookies)
        doc = pq(r.content)
        rows =  doc('tr.bibItemsEntry')
        out = []
        for r in rows:
            _k = {}
            cells = r.cssselect('td')
            try:
                item_num = cells[0].cssselect('input[type="radio"]')[0].attrib['value']
            except IndexError:
                item_num = None
            item, loc, call, status, barcode = tuple([c.text_content().strip().replace('\n', '') for c in cells])
            _k['id'] = item_num
            _k['location'] = loc
            _k['callnumber'] = call
            _k['status'] = status
            #_k['barcode'] = barcode
            #print i.value
            #print i.text
            out.append(_k)
        return out

    def place_hold(self, bib, item, pickup_location="ROCK"):
        """
        Place actual hold given bib and item.

        Article request for storage materials
        radio:i15976170
        inst:1)eppn
        2)12 1 2010
        3)6-13
        4)Don't deliver.  Test request.
        extpatid:
        extpatpw:
        name:
        code:
        pat_submit:Request Article
        """
        out = {}
        out['bib'] = bib
        out['item'] = item
        url = self.request_base.replace('{{bib}}', bib)
        payload = {
            'name' : self.name,
            'code' : self.barcode,
            'pat_submit':'xxx',
            'neededby_Month': 5,
            'neededby_Day': 6,
            'neededby_Year': 2015,
            'submit': 'SUBMIT',
            'loc': pickup_location,
            'radio': item,
            'inst': "Test request.  Don't deliver."
        }
        #post it
        rsp = self.session.post(url, data=payload)
        #Check for success message
        confirm_status = self._parse_hold_confirmation(rsp.content)
        out.update(confirm_status)
        return out

    def _parse_hold_confirmation(self, content):
        """
        Helper for parsing confirmation screen.
        """
        out = {
           'confirmed': False,
           'message': None
           }
        doc = pq(content)
        try:
            msg = doc('.style1')[0].text_content().encode('utf-8')
        except IndexError:
            msg = doc('p font[color="red"]').text()
            out['message'] = msg
            #These are failures.
            return out
        try:
            msg.index('was successful')
            out['confirmed'] = True
            return out
        except ValueError:
            return out

    def cancel_hold(self, cancel_key, seconds_to_wait=10):
        """
        The III database doesn't seem to cancel the hold in real time.

        We will try to cancel and verify the hold.  If not verified, we
        will try again, first pausing for a second.  We will try verify up to
        `seconds_to_wait`.  In testing, waiting up to 10 seconds was possible.
        """
        out = {}
        out['cancelled'] = False
        out['key'] = cancel_key
        out['patron_id'] = self.patron_id

        loc_key = cancel_key.replace('cancel', 'loc')
        payload = {
                   'currentsortorder':'current_pickup',
                   'currentsortorder':'current_pickup',
                   'updateholdssome': 'YES',
                   cancel_key: 'on',
                   loc_key: ''
        }
        url = self.opac_url + 'patroninfo~S7/%s/holds' % self.patron_id
        r = self.session.post(url, data=payload)
        elapsed = 0
        while True:
            log.debug("Attempting to verify canceled hold.")
            #Get all the holds and verify that this key isn't in the current hold set.
            current_holds = [h['key'] for h in self.get_holds()]
            #These are failures.
            if cancel_key in current_holds:
                # Wait a second
                log.debug("Waiting for one second.")
                time.sleep(1)
                elapsed += 1
                pass
            # Success
            else:
                break
            #Make sure we haven't passed to max seconds.
            if elapsed >= seconds_to_wait:
                raise Exception("Couldn't cancel hold in time specified.")
                break

        out['cancelled'] = True
        return out

    def cancel_all_holds(self):
        """
        Cancel all of a patron's holds.
        """
        payload = {
                   'currentsortorder':'current_pickup',
                   'currentsortorder':'current_pickup',
                   'cancelall':'YES'
                }

        out = {}
        out['cancelled'] = False

        url = self.opac_url + 'patroninfo~S7/%s/holds' % self.patron_id
        r = requests.post(url,
                          data=payload,
                          cookies=self.cookies)
        doc = pq(r.content)
        no = doc('#patron_functions').text()
        if no.rfind('No holds found'):
            out['cancelled'] = True
            return out
        else:
            return out

    def get_checkouts(self):
        """
        Get a list of items a user has checked out.
        """
        url = self.opac_url + 'patroninfo/%s/items' % self.patron_id

        rsp = self.session.get(url)
        content = rsp.content
        #Will raise if session expired message is found.
        self._validate_session(content)
        check_outs = self._parse_checkouts(content)
        return check_outs


    def _parse_checkouts(self, content):
        """
        Parse a given user's current checkouts.
        """
        doc = pq(content)
        t_rows = doc('.patFuncEntry')
        def _get(chunk, selector):
            """
            little util to get text by css selector.
            """
            return chunk.cssselect('td.%s' % selector)[0].text_content().strip()
        checkouts = [
            {
                 'key': row.cssselect('input')[0].attrib['id'],
                 'item': row.cssselect('input')[0].attrib['value'],
                 'title': _get(row, 'patFuncTitle'),
                 'barcode': _get(row, 'patFuncBarcode'),
                 'status': _get(row, 'patFuncStatus'),
                 'call_number': _get(row, 'patFuncCallNo'),
            }
            for row in t_rows
        ]
        return checkouts

    def renew_item(self):
        """
        post 1 - patroninfo~S7/x/items
        requestRenewSome:requestRenewSome
        currentsortorder:current_checkout
        renew0:i12445874
        currentsortorder:current_checkout

        post2 - /patroninfo~S7/x/items

        currentsortorder:current_checkout
        renew0:i12445874
        currentsortorder:current_checkout
        renewsome:YES

        parse renewed html for confirmation

        """
        pass

    def renew_all(self):
        pass

    def get_fines(self):
        """
        Parse the odd fines table.
        """
        #https://josiah.brown.edu/patroninfo~S7/x/overdues
        url = self.opac_url + 'patroninfo/%s/overdues' % self.patron_id
        r = requests.get(url,
                         cookies=self.cookies)
        doc = pq(r.content)
        out = {}
        out['total'] = doc('.patFuncFinesTotalAmt').text()
        fines = doc('table.patFunc tr')
        label = None
        amount = None
        fine_data = []
        #Skipping first row since it's a header and last row because it's the
        #total
        for fine in fines[1:-1]:
            val = doc(fine).text()
            if val.rfind('$') > -1:
                amount = val
            else:
                label = val

            if label and amount:
                fine_data.append(
                                 {'label': label,
                                 'amount': amount})
                label = None
                amount = None

        out['fines'] = fine_data
        return out